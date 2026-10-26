import csv
import random

import pytest

from src.generator.enrichment import on_campus_housing as h
from src.generator.enrichment.on_campus_housing import generate_on_campus_housing


def write_cohort(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Cohort", "Student_GUID", "Cohort_Term"])
        writer.writeheader()
        writer.writerows(rows)


def fake_students(n):
    cohorts = ["2021-22", "2022-23", "2023-24", "2024-25"]
    return {f"S{i:05d}": (cohorts[i % 4], h._TERMS[i % 3]) for i in range(n)}


def test_only_public_api_is_generate_on_campus_housing():
    public = [name for name in vars(h) if not name.startswith("_") and callable(getattr(h, name))
              and getattr(getattr(h, name), "__module__", None) == h.__name__]
    assert public == ["generate_on_campus_housing"]
    assert h.__all__ == ["generate_on_campus_housing"]


def test_next_academic_year():
    assert h._next_academic_year("2021-22") == "2022-23"
    assert h._next_academic_year("2099-00") == "2100-01"


def test_build_term_grid_covers_every_term_in_order():
    grid = h._build_term_grid("2021-22", "2022-23")
    assert grid == [
        ("2021-22", "FALL"), ("2021-22", "WINTER"), ("2021-22", "SPRING"), ("2021-22", "SUMMER"),
        ("2022-23", "FALL"), ("2022-23", "WINTER"), ("2022-23", "SPRING"), ("2022-23", "SUMMER"),
    ]


def test_read_students_keeps_earlier_cohort_and_uppercases_term(tmp_path):
    path = tmp_path / "cohort.csv"
    write_cohort(path, [
        {"Cohort": "2022-23", "Student_GUID": "A", "Cohort_Term": "Fall"},
        {"Cohort": "2021-22", "Student_GUID": "A", "Cohort_Term": "FALL"},
        {"Cohort": "2023-24", "Student_GUID": "B", "Cohort_Term": "SPRING"},
    ])
    assert h._read_students(path) == {"A": ("2021-22", "FALL"), "B": ("2023-24", "SPRING")}


def test_student_timeline_starts_at_cohort_term():
    grid = h._build_term_grid("2021-22", "2022-23")
    assert h._student_timeline("2022-23", "SPRING", grid) == [("2022-23", "SPRING"), ("2022-23", "SUMMER")]


def test_student_timeline_rejects_unknown_term():
    grid = h._build_term_grid("2021-22", "2021-22")
    with pytest.raises(ValueError):
        h._student_timeline("2030-31", "FALL", grid)


def test_simulate_stay_always_stays_when_retention_is_one():
    timeline = h._build_term_grid("2021-22", "2021-22")
    rows = h._simulate_stay(timeline, 1.0, random.Random(1))
    assert all(in_housing for _, _, in_housing in rows)
    assert rows[-1][:2] == timeline[-1]


def test_simulate_stay_leaves_after_one_term_when_retention_is_zero():
    timeline = h._build_term_grid("2021-22", "2022-23")
    rows = h._simulate_stay(timeline, 0.0, random.Random(3))
    flags = [in_housing for _, _, in_housing in rows]
    assert flags in ([True], [True, False])  # [True] only when entering in the last term
    if len(flags) == 2:
        assert timeline.index(rows[1][:2]) == timeline.index(rows[0][:2]) + 1


def test_simulate_stay_never_comes_back_after_leaving():
    timeline = h._build_term_grid("2021-22", "2024-25")
    rng = random.Random(7)
    for _ in range(500):
        flags = [in_housing for _, _, in_housing in h._simulate_stay(timeline, 0.6, rng)]
        assert flags[0] is True
        assert False not in flags[:-1]


def test_generate_share_of_students():
    rows = h._generate(fake_students(1000), 0.30, 0.60, seed=11)
    assert len({row["Student_GUID"] for row in rows}) == 300


def test_generate_retention_is_close_to_target():
    rows = h._generate(fake_students(20000), 0.30, 0.60, seed=12)
    by_student = {}
    for row in rows:
        by_student.setdefault(row["Student_GUID"], []).append(row["Uses_On_Campus_Housing"])
    stayed = left = 0
    for flags in by_student.values():
        for nxt in flags[1:]:
            stayed += nxt == "true"
            left += nxt == "false"
    assert abs(stayed / (stayed + left) - 0.60) < 0.02


def test_generate_rows_fall_inside_cohort_file_years():
    rows = h._generate(fake_students(400), 0.5, 0.6, seed=13)
    assert {row["Academic_Year"] for row in rows} <= {"2021-22", "2022-23", "2023-24", "2024-25"}
    assert {row["Academic_Term"] for row in rows} <= set(h._TERMS)


def test_generate_is_repeatable_for_a_seed():
    students = fake_students(300)
    assert h._generate(students, 0.3, 0.6, seed=5) == h._generate(students, 0.3, 0.6, seed=5)
    assert h._generate(students, 0.3, 0.6, seed=5) != h._generate(students, 0.3, 0.6, seed=6)


def cohort_of_100(tmp_path):
    cohort = tmp_path / "cohort.csv"
    write_cohort(cohort, [
        {"Cohort": c, "Student_GUID": f"G{i}", "Cohort_Term": "FALL"}
        for i, c in enumerate(["2021-22", "2022-23", "2023-24", "2024-25"] * 25)
    ])
    return cohort


def read_output(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def test_generate_on_campus_housing_writes_and_returns_rows(tmp_path):
    output = tmp_path / "out" / "on_campus_housing.csv"
    returned = generate_on_campus_housing(cohort_of_100(tmp_path), output, seed=9)
    fieldnames, rows = read_output(output)
    assert fieldnames == h._OUTPUT_COLUMNS
    assert rows == returned
    assert len({row["Student_GUID"] for row in rows}) == 30
    assert {row["Uses_On_Campus_Housing"] for row in rows} <= {"true", "false"}
    assert all(row["Source"] == "synthetic: src/generator/enrichment/on_campus_housing.py, seed 9" for row in rows)


def test_command_line_end_to_end(tmp_path):
    output = tmp_path / "on_campus_housing.csv"
    h._main(["--cohort", str(cohort_of_100(tmp_path)), "--output", str(output), "--seed", "9", "--share", "0.5"])
    _, rows = read_output(output)
    assert len({row["Student_GUID"] for row in rows}) == 50
