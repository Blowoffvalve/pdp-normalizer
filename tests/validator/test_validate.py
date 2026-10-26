"""Checks the validator: modes, run id, report, failed records and the full mock data (Milestone 2.1 SC-9 to SC-13)."""

import csv
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from src.validator import validate as v
from src.validator.validate import validate

BUILD = Path(__file__).resolve().parents[2]
CONFIG = BUILD / "config"
SAMPLE = BUILD / "data/input/sample"
AR = dict(cohort_config=CONFIG / "cohort_mapping.yaml", course_config=CONFIG / "course_mapping.yaml")


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def broken_files(tmp_path):
    """Sample rows with one error and one warning planted in each file."""
    cohort, course = read(SAMPLE / "pdp_cohort_data.csv"), read(SAMPLE / "pdp_course_data.csv")
    bad_student = cohort[1]["Student_GUID"]
    cohort[1]["Student_GUID"] = "not-a-guid"                        # error: pattern
    cohort[2]["Pell_Status_First_Year"] = "U"                       # warning: yes_no
    graded = next(i for i, r in enumerate(course) if r["Number_of_Credits_Attempted"] not in (".", ""))
    course[graded]["Number_of_Credits_Earned"] = "25"               # error: maximum 20
    orphan = next(i for i, r in enumerate(course) if r["Student_GUID"] == bad_student)
    return {"cohort": write(tmp_path / "cohort.csv", cohort), "course": write(tmp_path / "course.csv", course),
            "graded_row": graded + 1, "orphan_row": orphan + 1, "course_rows": len(course), "cohort_rows": len(cohort)}


def test_only_public_api_is_validate():
    public = [n for n, f in vars(v).items() if not n.startswith("_") and callable(f)
              and getattr(f, "__module__", None) == v.__name__]
    assert public == ["validate"]
    assert v.__all__ == ["validate"]


# Functions

@pytest.mark.parametrize("kwargs", [
    {},
    {"cohort": "a", "course": "b", "cohort_config": "c"},
    {"enrichment": "e"},
    {"enrichment": "e", "enrichment_config": "f", "cohort": "a"},
])
def test_inputs_takes_exactly_one_mode(kwargs):
    args = dict.fromkeys(["cohort", "course", "cohort_config", "course_config", "enrichment", "enrichment_config"])
    with pytest.raises(ValueError):
        v._inputs(**dict(args, **kwargs))


def test_inputs_orders_the_cohort_file_first():
    assert v._inputs("a", "b", "c", "d", None, None) == [("a", "c"), ("b", "d")]
    assert v._inputs(None, None, None, None, "e", "f") == [("e", "f")]


def test_run_id_sorts_by_time_and_is_unique():
    first, second = v._new_run_id(), v._new_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", first)
    assert first != second


def test_key_is_blank_only_when_the_whole_natural_key_is_blank():
    fields = {"course_id": {"primary_key": True}, "course_prefix": {"unique": "k"}, "course_number": {"unique": "k"}}
    assert v._key_is_blank(fields, {"course_prefix": None, "course_number": None})
    assert not v._key_is_blank(fields, {"course_prefix": "ENG", "course_number": None})
    assert not v._key_is_blank({"x": {}}, {"x": None})  # no key mapped: the row is always made


# Module

def test_a_clean_run_passes_every_row(tmp_path):
    summary = validate(cohort=SAMPLE / "pdp_cohort_data.csv", course=SAMPLE / "pdp_course_data.csv", **AR,
                       config_dir=CONFIG, logs_dir=tmp_path)
    for name, result in summary["results"].items():
        assert result["rows_failed"] == 0, name
        assert len(summary["passed_rows"][name]) == result["rows_read"]
    assert summary["failed_records"] is None


def test_error_rows_are_held_back_and_warning_rows_pass(tmp_path, broken_files):  # SC-11
    f = broken_files
    summary = validate(cohort=f["cohort"], course=f["course"], **AR, config_dir=CONFIG, logs_dir=tmp_path)
    cohort, course = summary["results"]["cohort.csv"], summary["results"]["course.csv"]
    assert cohort["rows_failed"] == 1 and 2 not in summary["passed_rows"]["cohort.csv"]
    assert 3 in summary["passed_rows"]["cohort.csv"]  # warning only
    assert cohort["failures"]["student.student_id#0 pattern"] == {"error": 1}
    assert cohort["failures"]["cohort_membership.pell_status_first_year#0 value_set"] == {"warning": 1}
    # The orphaned student's course rows fail must_exist, because its cohort row was held back.
    assert f["orphan_row"] not in summary["passed_rows"]["course.csv"]
    assert course["failures"]["student must_exist"]["error"] >= 1
    assert f["graded_row"] not in summary["passed_rows"]["course.csv"]


def test_report_and_failed_records_link_to_each_other(tmp_path, broken_files):  # SC-10
    f = broken_files
    summary = validate(cohort=f["cohort"], course=f["course"], **AR, config_dir=CONFIG, logs_dir=tmp_path,
                       run_id="20261025T000000Z-abcdef")
    run = "20261025T000000Z-abcdef"
    failures = read(tmp_path / "validation_report" / run / "failures.csv")
    failed = read(tmp_path / "failed_records" / run / "cohort.csv") + read(tmp_path / "failed_records" / run / "course.csv")
    by_id = {r["failure_id"]: r for r in failures}
    assert all(r["run_id"] == run for r in failures + failed)
    for row in failed:
        for failure_id in row["failure_ids"].split("|"):
            line = by_id[failure_id]
            assert line["source_row_number"] == row["source_row_number"]
            assert (tmp_path / line["failed_record"]).name == line["source_file"]
    errors = [r for r in failures if r["severity"] == "error"]
    assert all(r["failed_record"] for r in errors)
    held = {(Path(r["failed_record"]).name, r["source_row_number"]) for r in errors}
    assert held == {(name, r["source_row_number"]) for name in ("cohort.csv", "course.csv")
                    for r in read(tmp_path / "failed_records" / run / name)}
    original = read(f["cohort"])[1]
    assert {k: failed[0][k] for k in original} == original  # the source row, unchanged


def test_summary_records_what_was_checked(tmp_path):  # SC-12
    summary = validate(cohort=SAMPLE / "pdp_cohort_data.csv", course=SAMPLE / "pdp_course_data.csv", **AR,
                       config_dir=CONFIG, logs_dir=tmp_path)
    written = yaml.safe_load(open(Path(summary["report"]) / "summary.yaml"))
    assert written["run_id"] == summary["run_id"] and written["started_at"] <= written["finished_at"]
    files = {Path(f["path"]).name: f for f in written["files"]}
    assert set(files) >= {"pdp_cohort_data.csv", "pdp_course_data.csv", "destination_schema.yaml",
                          "value_sets.yaml", "cohort_mapping.yaml", "course_mapping.yaml"}
    for f in files.values():
        assert f["sha256"] == hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest()
    assert files["pdp_course_data.csv"]["role"] == "input" and files["value_sets.yaml"]["role"] == "config"


def test_summary_counts_repairs_unknowns_and_no_values_apart(tmp_path):  # SC-14, D6
    cohort = read(SAMPLE / "pdp_cohort_data.csv")
    cohort[0]["Race"] = "American Indian or Alaska"
    cohort[0]["GPA_Group_Term_1"] = "UK"
    cohort[1]["GPA_Group_Term_1"] = ""
    path = write(tmp_path / "cohort.csv", cohort)
    summary = validate(cohort=path, course=SAMPLE / "pdp_course_data.csv", **AR, config_dir=CONFIG, logs_dir=tmp_path)
    result = summary["results"]["cohort.csv"]
    assert result["repairs"]["student.race"] >= 1
    assert result["unknown"]["cohort_membership.gpa_group_term_1"] >= 1
    assert result["no_value"]["cohort_membership.gpa_group_term_1"] >= 1


def test_enrichment_mode_does_not_check_foreign_keys(tmp_path):  # SC-9, P9
    housing = read(BUILD / "data/input/enrichment/on_campus_housing.csv")[:5]
    housing[0]["Student_GUID"] = "F" * 32  # no such student
    path = write(tmp_path / "housing.csv", housing)
    summary = validate(enrichment=path, enrichment_config=CONFIG / "on_campus_housing_mapping.yaml",
                       config_dir=CONFIG, logs_dir=tmp_path)
    assert summary["results"]["housing.csv"]["rows_failed"] == 0
    assert summary["foreign_keys"].startswith("not checked")


def test_enrichment_rules_run(tmp_path):  # SC-9
    events = read(BUILD / "data/input/enrichment/institution_events.csv")
    events[0]["End_Date"] = "20251001"  # before its start date
    events[1]["Event_Type"] = "parade"
    path = write(tmp_path / "events.csv", events)
    summary = validate(enrichment=path, enrichment_config=CONFIG / "institution_events_mapping.yaml",
                       config_dir=CONFIG, logs_dir=tmp_path)
    failures = summary["results"]["events.csv"]["failures"]
    assert failures["institution_event.end_date#0 not_before"] == {"error": 1}
    assert failures["institution_event.event_type#0 enum"] == {"error": 1}


def test_a_bad_config_stops_before_reading_data(tmp_path):
    (tmp_path / "destination_schema.yaml").write_text("t: {a: {type: text, nullable: true}}\n")
    with pytest.raises(v.ConfigError):
        validate(enrichment="no-such-file.csv", enrichment_config=CONFIG / "on_campus_housing_mapping.yaml",
                 config_dir=tmp_path, logs_dir=tmp_path)
    assert not (tmp_path / "validation_report").exists()


# End to end

# Field audit finding 11, as worked out in the Plan's table under PQ-2 (SC-13).
EXPECTED = {
    "student.student_age": 0, "student.race": 0, "student.ethnicity": 0,
    "course_enrollment.math_or_english_gateway": 75504, "course_enrollment.course_cip": 4217,
    "course_enrollment.course_instructor_rank": 77800, "course_enrollment.course_begin_date": 0,
    "course_enrollment.course_end_date": 0, "course_enrollment.number_of_credits_attempted": 0,
    "course_enrollment.number_of_credits_earned": 0, "course.course_prefix": 0, "course.course_number": 0,
    "course_enrollment.course_name": 0,
}


def test_full_mock_data_matches_the_field_audit(tmp_path):  # SC-13
    summary = validate(cohort=BUILD / "data/input/pdp_cohort_data.csv", course=BUILD / "data/input/pdp_course_data.csv",
                       **AR, config_dir=CONFIG, logs_dir=tmp_path)
    course = summary["results"]["pdp_course_data.csv"]
    counts = {column: sum(sum(c.values()) for rule, c in course["failures"].items() if rule.startswith(column + "#")
                          or rule.startswith(column + " "))
              for column in EXPECTED}
    assert counts == EXPECTED
    assert course["repairs"]["student.race"] == 1798
    assert summary["results"]["pdp_cohort_data.csv"]["repairs"]["student.race"] == 225


def test_command_line_end_to_end(tmp_path, capsys):
    code = v._main(["--cohort", str(SAMPLE / "pdp_cohort_data.csv"), "--course", str(SAMPLE / "pdp_course_data.csv"),
                    "--cohort_config", str(AR["cohort_config"]), "--course_config", str(AR["course_config"]),
                    "--config_dir", str(CONFIG), "--logs_dir", str(tmp_path), "--run_id", "20261025T000000Z-000001"])
    out = capsys.readouterr().out
    assert code == 0
    assert "run id: 20261025T000000Z-000001" in out
    assert (tmp_path / "validation_report/20261025T000000Z-000001/summary.yaml").exists()


def test_command_line_exits_2_on_a_bad_config(tmp_path, capsys):
    (tmp_path / "destination_schema.yaml").write_text("t: {a: {type: text, nullable: true}}\n")
    code = v._main(["--enrichment", "x.csv", "--enrichment_config", str(CONFIG / "on_campus_housing_mapping.yaml"),
                    "--config_dir", str(tmp_path), "--logs_dir", str(tmp_path)])
    assert code == 2
    assert "invalid config" in capsys.readouterr().err
