"""Generate the synthetic on-campus housing enrichment file (Milestone 1 spec, D11).

The only public API is generate_on_campus_housing(). A share of students use on-campus
housing at some point. Each one enters in a random term of their own range, stays each
following term with a fixed probability, and never comes back after leaving. Rows are
written only for housing students: true for each term in housing, then one false row for
the term they leave in.

Run from the build folder:
    python -m src.generator.enrichment.on_campus_housing
"""

import argparse
import csv
import random
from pathlib import Path

__all__ = ["generate_on_campus_housing"]

_TERMS = ("FALL", "WINTER", "SPRING", "SUMMER")  # dictionary order within an academic year
_SOURCE = "synthetic: src/generator/enrichment/on_campus_housing.py, seed {seed}"
_OUTPUT_COLUMNS = ["Student_GUID", "Academic_Year", "Academic_Term", "Uses_On_Campus_Housing", "Source"]


def generate_on_campus_housing(
    cohort_path="data/input/pdp_cohort_data.csv",
    output_path="data/input/enrichment/on_campus_housing.csv",
    share=0.30,
    retention=0.60,
    seed=2026,
):
    """Write the on-campus housing enrichment file and return the rows written.

    share: share of students who ever use on-campus housing.
    retention: chance of staying each term after entering.
    The same seed always produces the same file.
    """
    students = _read_students(cohort_path)
    rows = _generate(students, share, retention, seed)
    _write_rows(rows, output_path)
    return rows


def _next_academic_year(academic_year):
    """'2021-22' -> '2022-23'."""
    start = int(academic_year[:4]) + 1
    return f"{start}-{str(start + 1)[-2:]}"


def _build_term_grid(first_year, last_year, terms=_TERMS):
    """Every (academic_year, term) from first_year to last_year, in order."""
    grid = []
    year = first_year
    while year <= last_year:
        grid.extend((year, term) for term in terms)
        year = _next_academic_year(year)
    return grid


def _read_students(cohort_path):
    """One (cohort, cohort_term) per student. A student with two cohort rows keeps the earlier one."""
    students = {}
    with open(cohort_path, newline="") as f:
        for row in csv.DictReader(f):
            entry = (row["Cohort"], row["Cohort_Term"].upper())
            guid = row["Student_GUID"]
            if guid not in students or entry < students[guid]:
                students[guid] = entry
    return students


def _student_timeline(cohort, cohort_term, grid):
    """The part of the grid from the student's cohort term onwards."""
    if (cohort, cohort_term) not in grid:
        raise ValueError(f"cohort term {cohort} {cohort_term} is not in the term grid")
    return grid[grid.index((cohort, cohort_term)):]


def _simulate_stay(timeline, retention, rng):
    """Housing rows for one student: (year, term, in_housing). Enters at a random term."""
    start = rng.randrange(len(timeline))
    rows = [(*timeline[start], True)]
    for year, term in timeline[start + 1:]:
        if rng.random() < retention:
            rows.append((year, term, True))
        else:
            rows.append((year, term, False))
            break
    return rows


def _generate(students, share, retention, seed, terms=_TERMS):
    """All housing rows, as dicts with _OUTPUT_COLUMNS. Same seed, same rows."""
    rng = random.Random(seed)
    cohorts = [cohort for cohort, _ in students.values()]
    grid = _build_term_grid(min(cohorts), max(cohorts), terms)
    guids = sorted(students)
    users = sorted(rng.sample(guids, round(len(guids) * share)))
    source = _SOURCE.format(seed=seed)
    rows = []
    for guid in users:
        timeline = _student_timeline(*students[guid], grid)
        for year, term, in_housing in _simulate_stay(timeline, retention, rng):
            rows.append({
                "Student_GUID": guid,
                "Academic_Year": year,
                "Academic_Term": term,
                "Uses_On_Campus_Housing": "true" if in_housing else "false",
                "Source": source,
            })
    return rows


def _write_rows(rows, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Generate the synthetic on-campus housing enrichment file.")
    parser.add_argument("--cohort", default="data/input/pdp_cohort_data.csv", help="cohort Analysis-Ready File")
    parser.add_argument("--output", default="data/input/enrichment/on_campus_housing.csv")
    parser.add_argument("--share", type=float, default=0.30, help="share of students who ever use on-campus housing")
    parser.add_argument("--retention", type=float, default=0.60, help="chance of staying each term after entering")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)

    rows = generate_on_campus_housing(args.cohort, args.output, args.share, args.retention, args.seed)
    users = len({row["Student_GUID"] for row in rows})
    print(f"{len(rows)} rows for {users} students written to {args.output}")


if __name__ == "__main__":
    _main()
