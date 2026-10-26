"""Checks the sample builder and the published samples (spec SC-3, SC-4, SC-5)."""

import csv
from datetime import date
from pathlib import Path

import pytest
import yaml

from src.sample import build_sample as b
from src.sample.build_sample import build_sample

BUILD = Path(__file__).resolve().parents[2]
SCHEMA = yaml.safe_load(open(BUILD / "config/destination_schema.yaml"))
OUTPUT = BUILD / "data/output/sample"
INPUT = BUILD / "data/input/sample"


def read(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def table(name):
    return read(OUTPUT / f"{name}.csv")[1]


def test_only_public_api_is_build_sample():
    public = [n for n, v in vars(b).items() if not n.startswith("_") and callable(v)
              and getattr(v, "__module__", None) == b.__name__]
    assert public == ["build_sample"]
    assert b.__all__ == ["build_sample"]


# Functions

@pytest.mark.parametrize("value,type_name,expected", [
    ("abc", "str", "abc"), ("", "str", None), ("UK", "str", "UK"),
    ("3", "int", 3), ("-1", "int", -1), (".", "int", None),
    ("3", "float", 3.0), ("1.7", "float", 1.7), (".", "float", None), ("UK", "float", None),
    ("1", "bool", True), ("false", "bool", False),
    ("20240319", "date", date(2024, 3, 19)), (".", "date", None),
])
def test_convert(value, type_name, expected):
    assert b._convert(value, type_name) == (expected, True)


def test_convert_reports_values_that_do_not_fit_the_type():
    assert b._convert("abc", "int") == (None, False)
    assert b._convert("2024-03-19", "date") == (None, False)


def test_identity_uses_primary_key_then_unique_group():
    assert b._identity(SCHEMA, "student", {"student_id": "A"}) == ("A",)
    assert b._identity(SCHEMA, "course", {"course_prefix": "ENG", "course_number": "101"}) == ("ENG", "101")
    with pytest.raises(ValueError):
        b._identity({"t": {"id": {"type": "int", "nullable": False, "primary_key": True}}}, "t", {})


def test_store_applies_on_conflict_actions():
    store = b._Store(SCHEMA)
    store.upsert("cohort_membership", {"student_id": "A", "cohort": "2022-23", "cohort_term": "FALL"}, {})
    actions = {"cohort_term": "append"}
    row = store.upsert("cohort_membership", {"student_id": "A", "cohort": "2022-23", "cohort_term": "SPRING"}, actions)
    assert row["cohort_term"] == "FALL"  # append keeps the existing value until Q-C is decided
    row = store.upsert("cohort_membership", {"student_id": "A", "cohort": "2022-23", "cohort_term": "SPRING"},
                       {"cohort_term": "keep"})
    assert row["cohort_term"] == "FALL"
    row = store.upsert("cohort_membership", {"student_id": "A", "cohort": "2022-23", "cohort_term": "SPRING"},
                       {"cohort_term": "replace"})
    assert row["cohort_term"] == "SPRING"
    assert store.collisions == {("cohort_membership", "cohort_term", "append"): 1,
                                ("cohort_membership", "cohort_term", "keep"): 1,
                                ("cohort_membership", "cohort_term", "replace"): 1}


def test_store_hands_out_surrogate_ids_and_finds_rows_again():
    store = b._Store(SCHEMA)
    first = store.upsert("course", {"course_prefix": "ENG", "course_number": "101"}, {})
    second = store.upsert("course", {"course_prefix": "MATH", "course_number": "100"}, {})
    again = store.upsert("course", {"course_prefix": "ENG", "course_number": "101"}, {})
    assert (first["course_id"], second["course_id"], again["course_id"]) == (1, 2, 1)


def test_fill_term_order_puts_unknown_last():
    terms = [{"term_id": 1, "academic_year": "UNKNOWN", "academic_term": "UNKNOWN"},
             {"term_id": 2, "academic_year": "2022-23", "academic_term": "SPRING"},
             {"term_id": 3, "academic_year": "2022-23", "academic_term": "FALL"}]
    b._fill_term_order(terms)
    assert [t["sort_order"] for t in terms] == [3, 2, 1]


def test_fill_attempt_numbers_counts_retakes_by_term():
    terms = [{"term_id": 1, "sort_order": 2}, {"term_id": 2, "sort_order": 1}]
    rows = [{"student_id": "A", "course_id": 7, "term_id": 1, "provider_id": 1, "source_row_number": 1},
            {"student_id": "A", "course_id": 7, "term_id": 2, "provider_id": 1, "source_row_number": 2},
            {"student_id": "A", "course_id": None, "term_id": 2, "provider_id": 1, "source_row_number": 3}]
    b._fill_attempt_numbers(rows, terms)
    assert [r.get("attempt_number") for r in rows] == [2, 1, None]


# Published samples

@pytest.mark.parametrize("name", SCHEMA)
def test_sample_header_matches_the_constants(name):
    fieldnames, _ = read(OUTPUT / f"{name}.csv")
    assert fieldnames == list(SCHEMA[name])  # SC-3


def test_sample_foreign_keys_resolve():
    for name, columns in SCHEMA.items():
        for column, props in columns.items():
            if "foreign_key" not in props:
                continue
            ref_table, ref_column = props["foreign_key"].split(".")
            known = {r[ref_column] for r in table(ref_table)}
            for row in table(name):
                assert row[column] == "" and props["nullable"] or row[column] in known, (name, column, row[column])  # SC-4


def test_sample_has_about_ten_students():
    assert len(table("student")) == 10
    assert len(table("institution")) == 1
    assert len(table("data_provider")) == 4


def test_sample_contains_every_quirk():  # SC-5
    memberships = [r["student_id"] for r in table("cohort_membership")]
    assert len(memberships) > len(set(memberships)), "a student in two cohorts"
    enrollments = table("course_enrollment")
    assert any(r["attempt_number"] == "2" for r in enrollments), "a retake"
    assert any(r["section_id"] == "" and r["course_id"] == "" for r in enrollments), "a row with no course record"
    unknown = next(t["term_id"] for t in table("term") if t["academic_year"] == "UNKNOWN")
    assert any(r["term_id"] == unknown for r in enrollments), "a row in the unknown term"
    assert any(r["term_id"] for r in table("student_attribute")), "a term-scoped student attribute"
    _, cohort = read(INPUT / "pdp_cohort_data.csv")
    _, course = read(INPUT / "pdp_course_data.csv")
    terms = {(r["Student_GUID"], r["Cohort"]): r["Cohort_Term"] for r in cohort}
    assert any(r["Cohort_Term"] != terms[(r["Student_GUID"], r["Cohort"])] for r in course), "a Cohort_Term collision"


def test_sample_keeps_the_cohort_file_value_for_the_cohort_term_collision():
    _, cohort = read(INPUT / "pdp_cohort_data.csv")
    built = {(r["student_id"], r["cohort"]): r["cohort_term"] for r in table("cohort_membership")}
    assert built == {(r["Student_GUID"], r["Cohort"]): r["Cohort_Term"] for r in cohort}


def test_sample_row_counts_match_the_sample_input():
    _, course = read(INPUT / "pdp_course_data.csv")
    _, cohort = read(INPUT / "pdp_cohort_data.csv")
    assert len(table("course_enrollment")) == len(course)
    assert len(table("cohort_membership")) == len(cohort)


# End to end

def test_build_sample_reproduces_the_published_samples(tmp_path):
    summary = build_sample(input_dir=BUILD / "data/input", config_dir=BUILD / "config",
                           sample_input_dir=tmp_path / "in", sample_output_dir=tmp_path / "out")
    for name in SCHEMA:
        assert read(tmp_path / "out" / f"{name}.csv") == read(OUTPUT / f"{name}.csv"), name
    assert summary["unconverted"] == {}
    assert summary["collisions"] == {("cohort_membership", "cohort_term", "append"): 1}


def test_command_line_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(BUILD)
    calls = {}
    monkeypatch.setattr(b, "build_sample", lambda **kw: calls.update(kw) or {
        "students": ["A" * 32], "reasons": {"A" * 32: ["random"]}, "rows": {}, "collisions": {}, "unconverted": {}})
    b._main(["--students", "5", "--seed", "3"])
    assert calls == {"students": 5, "seed": 3}
    assert "AAAAAAAA" in capsys.readouterr().out
