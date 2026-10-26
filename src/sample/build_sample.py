"""Build the Milestone 1 sample files (spec D5, P6).

The only public API is build_sample(). It picks about 10 students so that every quirk in
SC-5 appears, writes their raw rows to data/input/sample/, and builds every table in
config/destination_schema.yaml from them by reading the mapping configs. The result goes to
data/output/sample/, one CSV per table.

Source values are read through src/rules (Milestone 2.1 D5 to D7): repairs, codes for "no value"
and "unknown", and the conversion to each column's type all come from the configs.

On-conflict actions are applied the simple way the loader will: `replace` overwrites, `keep`
keeps the existing value, and `append` also keeps the existing value until spec Q-C decides
where an appended value is stored. Collisions are counted and printed, not written to a file.

Run from the build folder:
    python -m src.sample.build_sample
"""

import argparse
import csv
import hashlib
import random
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from src.rules.rules import load_rules, read_value

__all__ = ["build_sample"]

_TERM_ORDER = {"FALL": 0, "WINTER": 1, "SPRING": 2, "SUMMER": 3}
_UNKNOWN = "UNKNOWN"
_PLACEHOLDER_INSTITUTION = {"institution_id": 1, "name": "Placeholder institution", "is_placeholder": True}
# Load order, file names inside data/input/, mapping config and data provider name.
_SOURCES = [
    ("pdp_cohort_data.csv", "cohort_mapping.yaml", "Achieving the Dream"),
    ("pdp_course_data.csv", "course_mapping.yaml", "Achieving the Dream"),
    ("enrichment/on_campus_housing.csv", "on_campus_housing_mapping.yaml", "synthetic: on-campus housing generator"),
    ("enrichment/institution_events.csv", "institution_events_mapping.yaml", "public: institution events"),
]
# Tables in the order a source row fills them, so foreign keys can point at rows made earlier.
_TABLE_ORDER = ["term", "course", "student", "cohort_membership", "course_enrollment",
                "student_attribute", "institution_event"]


def build_sample(input_dir="data/input", config_dir="config", sample_input_dir="data/input/sample",
                 sample_output_dir="data/output/sample", students=10, seed=2026,
                 loaded_at="2026-10-25T00:00:00"):
    """Write the sample input and output files. Returns a summary of what was built."""
    input_dir, config_dir = Path(input_dir), Path(config_dir)
    rules = load_rules(config_dir)
    schema = rules["schema"]
    raw = {name: _read_csv(input_dir / name) for name, _, _ in _SOURCES}
    chosen, reasons = _pick_students(raw, students, seed)
    _write_sample_input(raw, chosen, Path(sample_input_dir))
    tables, collisions, unconverted = _build_tables(rules, Path(sample_input_dir),
                                                    datetime.fromisoformat(loaded_at))
    _write_tables(schema, tables, Path(sample_output_dir))
    return {"students": chosen, "reasons": reasons,
            "rows": {t: len(rows) for t, rows in tables.items()},
            "collisions": collisions, "unconverted": unconverted}


def _read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _pick_students(raw, count, seed):
    """One student per SC-5 quirk (the one with the fewest course rows), then random ones up to count."""
    cohort, course = raw["pdp_cohort_data.csv"], raw["pdp_course_data.csv"]
    housing = raw["enrichment/on_campus_housing.csv"]
    course_rows = defaultdict(list)
    for row in course:
        course_rows[row["Student_GUID"]].append(row)
    cohort_terms = {(r["Student_GUID"], r["Cohort"]): r["Cohort_Term"] for r in cohort}
    cohort_count = Counter(r["Student_GUID"] for r in cohort)
    housing_flags = defaultdict(set)
    for row in housing:
        housing_flags[row["Student_GUID"]].add(row["Uses_On_Campus_Housing"])

    def retake(rows):
        terms = defaultdict(set)
        for r in rows:
            if r["Course_Prefix"]:
                terms[(r["Course_Prefix"], r["Course_Number"])].add((r["Academic_Year"], r["Academic_Term"]))
        return any(len(t) > 1 for t in terms.values())

    quirks = {
        "in two cohorts": lambda g, rows: cohort_count[g] > 1,
        "course-file Cohort_Term collides with the cohort file": lambda g, rows: any(
            r["Cohort_Term"] != cohort_terms.get((g, r["Cohort"])) for r in rows),
        "retake": lambda g, rows: retake(rows),
        "course row with no course record or Section_ID": lambda g, rows: any(
            not r["Course_Prefix"] and r["Academic_Year"] for r in rows),
        "course row in the unknown term": lambda g, rows: any(not r["Academic_Year"] for r in rows),
        "on-campus housing, entering and leaving": lambda g, rows: housing_flags[g] == {"true", "false"},
    }
    candidates = sorted(course_rows, key=lambda g: (len(course_rows[g]), g))
    chosen, reasons = [], {}
    for reason, test in quirks.items():
        if any(test(g, course_rows[g]) for g in chosen):
            reasons.setdefault(next(g for g in chosen if test(g, course_rows[g])), []).append(reason)
            continue
        guid = next(g for g in candidates if g not in chosen and test(g, course_rows[g]))
        chosen.append(guid)
        reasons.setdefault(guid, []).append(reason)
    rest = [g for g in candidates if g not in chosen and len(course_rows[g]) <= 12]
    for guid in random.Random(seed).sample(rest, max(0, count - len(chosen))):
        chosen.append(guid)
        reasons[guid] = ["random"]
    return chosen, reasons


def _write_sample_input(raw, chosen, sample_input_dir):
    keep = set(chosen)
    for name, rows in raw.items():
        selected = rows if "Student_GUID" not in rows[0] else [r for r in rows if r["Student_GUID"] in keep]
        path = sample_input_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(selected)


def _identity(schema, table, row):
    """The natural key that finds an existing row: the primary key if the mapping supplies it,
    otherwise the table's unique group."""
    columns = schema[table]
    pk = [c for c, p in columns.items() if p.get("primary_key")]
    if all(row.get(c) is not None for c in pk):
        return tuple(row[c] for c in pk)
    unique = [c for c, p in columns.items() if p.get("unique")]
    if not unique:
        raise ValueError(f"{table} has no primary key in the mapping and no unique group to find rows by")
    return tuple(row.get(c) for c in unique)


class _Store:
    """Rows per table, found by identity, with surrogate ids handed out in insert order."""

    def __init__(self, schema):
        self.schema = schema
        self.rows = defaultdict(dict)
        self.collisions = Counter()

    def exists(self, table, row):
        """True when the row's primary key is fully known and already stored."""
        pk = [c for c, p in self.schema[table].items() if p.get("primary_key")]
        return all(row.get(c) is not None for c in pk) and tuple(row[c] for c in pk) in self.rows[table]

    def upsert(self, table, row, actions):
        key = _identity(self.schema, table, row)
        existing = self.rows[table].get(key)
        if existing is None:
            pk = [c for c, p in self.schema[table].items() if p.get("primary_key")]
            if len(pk) == 1 and row.get(pk[0]) is None:
                row[pk[0]] = len(self.rows[table]) + 1
            self.rows[table][key] = row
            return row
        for column, value in row.items():
            old = existing.get(column)
            if value is None or old == value:
                continue
            action = actions.get(column, "replace")
            if old is None or action == "replace":
                existing[column] = value
            if old is not None:
                self.collisions[(table, column, action)] += 1
        return existing


def _build_tables(rules, sample_input_dir, loaded_at):
    schema = rules["schema"]
    store = _Store(schema)
    store.upsert("institution", dict(_PLACEHOLDER_INSTITUTION), {})
    unconverted = Counter()
    for name, config_name, provider_name in _SOURCES:
        path = sample_input_dir / name
        entries = rules["mappings"][config_name]
        rows = _read_csv(path)
        provider = store.upsert("data_provider", {
            "name": provider_name, "source_file": f"data/input/{name}",
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "row_count": len(rows), "loaded_at": loaded_at}, {})
        for number, source_row in enumerate(rows, start=1):
            pending, actions = defaultdict(dict), defaultdict(dict)
            for entry in entries:
                if "dropped" in entry:
                    continue
                table, column = entry["destination_table"], entry["destination_column"]
                _, value, problem = read_value(rules, entry, source_row.get(entry.get("source"), ""))
                if problem:
                    unconverted[(table, column)] += 1
                pending[table][column] = value
                actions[table][column] = entry["on_conflict"]
            made = {}
            for table in _TABLE_ORDER:
                if table not in pending:
                    continue
                row = pending[table]
                if table == "course" and not (row.get("course_prefix") and row.get("course_number")):
                    continue  # no course record on this row (spec P9)
                for column, props in schema[table].items():
                    target = props.get("foreign_key", "")
                    if row.get(column) is None and target:
                        ref_table, ref_column = target.split(".")
                        if ref_table in made:
                            row[column] = made[ref_table][ref_column]
                        elif store.exists(table, row):
                            continue  # an existing row keeps its own provider and institution
                        elif ref_table == "data_provider":
                            row[column] = provider["provider_id"]
                        elif ref_table == "institution":
                            row[column] = _PLACEHOLDER_INSTITUTION["institution_id"]
                if "source_row_number" in schema[table]:
                    row["source_row_number"] = number
                made[table] = store.upsert(table, row, actions[table])
    tables = {t: list(store.rows[t].values()) for t in schema}
    _fill_term_order(tables["term"])
    _fill_attempt_numbers(tables["course_enrollment"], tables["term"])
    return tables, dict(store.collisions), dict(unconverted)


def _fill_term_order(terms):
    def order(t):
        if t["academic_year"] == _UNKNOWN:
            return ("9999", 9)
        return (t["academic_year"], _TERM_ORDER.get(t["academic_term"], 8))
    for position, term in enumerate(sorted(terms, key=order), start=1):
        term["sort_order"] = position


def _fill_attempt_numbers(enrollments, terms):
    """Order of a student's rows for the same course, by term, then by source row. Blank when there is no course."""
    term_order = {t["term_id"]: t["sort_order"] for t in terms}
    groups = defaultdict(list)
    for row in enrollments:
        if row.get("course_id") is not None:
            groups[(row["student_id"], row["course_id"])].append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: (term_order[r["term_id"]], r["provider_id"], r["source_row_number"]))
        for attempt, row in enumerate(rows, start=1):
            row["attempt_number"] = attempt


def _format(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _write_tables(schema, tables, sample_output_dir):
    sample_output_dir.mkdir(parents=True, exist_ok=True)
    for table, columns in schema.items():
        with open(sample_output_dir / f"{table}.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(list(columns))
            for row in tables[table]:
                writer.writerow([_format(row.get(c)) for c in columns])


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Build the Milestone 1 sample input and output files.")
    parser.add_argument("--students", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    summary = build_sample(students=args.students, seed=args.seed)
    for guid in summary["students"]:
        print(f"{guid[:8]}…  {', '.join(summary['reasons'][guid])}")
    print("rows:", summary["rows"])
    print("collisions (table, column, action): count:", summary["collisions"])
    print("values that didn't convert to their type:", summary["unconverted"])


if __name__ == "__main__":
    _main()
