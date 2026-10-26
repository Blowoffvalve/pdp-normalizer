"""Validate source files against the rules in config/ (Milestone 2.1 spec, P4, P5, P9).

The only public API is validate(). It runs in one of the two modes in 04-build/AGENTS.md: both
Analysis-Ready Files with their mapping configs, or one enrichment file with its config. Every
run gets a run id and writes, under logs/:
- validation_report/<run_id>/summary.yaml: what was checked (files and their SHA-256), counts
  of rows, failures per rule, repairs, unknowns and no-values per column.
- validation_report/<run_id>/failures.csv: one line per failure.
- failed_records/<run_id>/<source file>.csv: the rows that failed an `error` rule, unchanged,
  after three link columns (run_id, source_row_number, failure_ids).
Rows that fail only `warning` rules are reported and pass on to loading.

Run from the build folder:
    python -m src.validator.validate --cohort data/input/pdp_cohort_data.csv \\
        --course data/input/pdp_course_data.csv \\
        --cohort_config config/cohort_mapping.yaml --course_config config/course_mapping.yaml
    python -m src.validator.validate --enrichment data/input/enrichment/on_campus_housing.csv \\
        --enrichment_config config/on_campus_housing_mapping.yaml
Exit code 0 when the run finishes, whatever it found; 2 when a config is invalid.
"""

import argparse
import csv
import hashlib
import secrets
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.rules.rules import BLANK, DEFAULT, UNKNOWN, VALUE, ConfigError, check_value, load_rules, read_value

__all__ = ["validate"]

_FAILURE_COLUMNS = ["failure_id", "run_id", "source_file", "source_row_number", "destination_table",
                    "destination_column", "value", "rule", "severity", "failed_record"]
_LINK_COLUMNS = ["run_id", "source_row_number", "failure_ids"]


def validate(cohort=None, course=None, cohort_config=None, course_config=None, enrichment=None,
             enrichment_config=None, config_dir="config", logs_dir="logs", run_id=None):
    """Validate one run's files and write its report and failed records.

    Returns the summary written to summary.yaml, plus "passed_rows": for each input file, the
    row numbers (1 = first data row) that pass on to loading. Raises ConfigError when a config
    is invalid, before any data is read.
    """
    inputs = _inputs(cohort, course, cohort_config, course_config, enrichment, enrichment_config)
    rules = load_rules(config_dir, [config for _, config in inputs])
    run_id = run_id or _new_run_id()
    started = datetime.now(timezone.utc)
    report_dir = Path(logs_dir) / "validation_report" / run_id
    failed_dir = Path(logs_dir) / "failed_records" / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    mode = "enrichment" if enrichment else "analysis_ready_files"
    summary = {"run_id": run_id, "mode": mode, "started_at": started.isoformat(timespec="seconds"),
               "files": [{"path": str(p), "role": role, "sha256": _sha256(p)}
                         for role, paths in (("input", [i for i, _ in inputs]), ("config", rules["files"]))
                         for p in map(Path, paths)],
               "foreign_keys": "checked against the cohort file of this run" if mode != "enrichment"
               else "not checked: enrichment mode, no database (P9)",
               "results": {}}
    passed_rows, seen = {}, defaultdict(set)
    counter = iter(range(1, 10 ** 12))
    with open(report_dir / "failures.csv", "w", newline="") as report:
        writer = csv.writer(report)
        writer.writerow(_FAILURE_COLUMNS)
        for source_path, config_path in inputs:
            result, passed = _validate_file(rules, Path(source_path), Path(config_path).name, mode, seen,
                                            run_id, writer, counter, failed_dir, Path(logs_dir))
            summary["results"][Path(source_path).name] = result
            passed_rows[Path(source_path).name] = passed
    summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary["report"] = str(report_dir)
    summary["failed_records"] = str(failed_dir) if failed_dir.exists() else None
    with open(report_dir / "summary.yaml", "w") as f:
        yaml.safe_dump(summary, f, sort_keys=False, allow_unicode=True, width=120)
    return dict(summary, passed_rows=passed_rows)


def _inputs(cohort, course, cohort_config, course_config, enrichment, enrichment_config):
    """The run's (file, mapping config) pairs, in load order. Exactly one mode (AGENTS.md)."""
    analysis_ready = [cohort, course, cohort_config, course_config]
    if enrichment or enrichment_config:
        if any(analysis_ready) or not (enrichment and enrichment_config):
            raise ValueError("enrichment mode takes only --enrichment and --enrichment_config")
        return [(enrichment, enrichment_config)]
    if not all(analysis_ready):
        raise ValueError("give --cohort, --course, --cohort_config and --course_config, "
                         "or --enrichment and --enrichment_config")
    return [(cohort, cohort_config), (course, course_config)]


def _new_run_id():
    """Start time in UTC plus 6 random hexadecimal characters, so run ids sort by time (P5)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_file(rules, source_path, config_name, mode, seen, run_id, writer, counter, failed_dir, logs_dir):
    entries = [e for e in rules["mappings"][config_name] if "dropped" not in e]
    failures_by_rule = defaultdict(Counter)
    repairs, unknowns, no_values = Counter(), Counter(), Counter()
    passed, failed_count, read = [], 0, 0
    failed_path = failed_dir / f"{source_path.name}"
    failed_writer, failed_file = None, None
    with open(source_path, newline="") as f:
        reader = csv.DictReader(f)
        for number, source_row in enumerate(reader, start=1):
            read += 1
            values, problems = _check_row(rules, entries, source_row, mode, seen, repairs, unknowns, no_values)
            errors = [p for p in problems if p[4] == "error"]
            ids = []
            for table, column, value, rule, severity in problems:
                failure_id = next(counter)
                ids.append(failure_id)
                failures_by_rule[rule][severity] += 1
                link = str((failed_path).relative_to(logs_dir)) if errors else ""
                writer.writerow([failure_id, run_id, source_path.name, number, table, column, value, rule, severity, link])
            if errors:
                failed_count += 1
                if failed_writer is None:
                    failed_dir.mkdir(parents=True, exist_ok=True)
                    failed_file = open(failed_path, "w", newline="")
                    failed_writer = csv.writer(failed_file)
                    failed_writer.writerow(_LINK_COLUMNS + reader.fieldnames)
                failed_writer.writerow([run_id, number, "|".join(map(str, ids))] +
                                       [source_row[c] for c in reader.fieldnames])
                continue
            passed.append(number)
            _remember(rules["schema"], values, seen)
    if failed_file:
        failed_file.close()
    result = {"rows_read": read, "rows_passed": len(passed), "rows_failed": failed_count,
              "failed_records": str(failed_path) if failed_count else None,
              "failures": {rule: dict(counts) for rule, counts in sorted(failures_by_rule.items())},
              "repairs": dict(sorted(repairs.items())), "unknown": dict(sorted(unknowns.items())),
              "no_value": dict(sorted(no_values.items()))}
    return result, passed


def _check_row(rules, entries, source_row, mode, seen, repairs, unknowns, no_values):
    """Read and check one source row (P4). Returns its stored values and its failures, each
    failure being (table, column, source value, rule, severity)."""
    schema = rules["schema"]
    values, real, raw_values, problems = defaultdict(dict), {}, {}, []
    must_exist = defaultdict(list)
    for entry in entries:
        table, column = entry["destination_table"], entry["destination_column"]
        raw = str(entry["value"]) if "value" in entry else source_row.get(entry["source"], "")
        state, value, problem = read_value(rules, entry, raw)
        name = f"{table}.{column}"
        if raw in entry.get("value_map", {}):
            repairs[name] += 1
        if state == UNKNOWN:
            unknowns[name] += 1
        elif state in (BLANK, DEFAULT):
            no_values[name] += 1
        if problem:
            problems.append((table, column, raw, f"{name} type", "error"))
        values[table][column] = value
        real[(table, column)] = state == VALUE and problem is None
        raw_values[(table, column)] = raw
        if entry.get("must_exist"):
            must_exist[table].append(column)
    skipped = {t for t in values if _key_is_blank(schema[t], values[t])}
    for (table, column), is_real in real.items():
        if table in skipped:
            continue
        value = values[table][column]
        if value is None and not schema[table][column]["nullable"]:
            problems.append((table, column, raw_values[(table, column)], f"{table}.{column} required", "error"))
            continue
        for rule, severity in check_value(rules, table, column, value, values, real=is_real):
            problems.append((table, column, raw_values[(table, column)], rule, severity))
    if mode != "enrichment":  # P9: enrichment foreign keys are the loader's to check
        for table, columns in must_exist.items():
            key = _primary_key(schema[table], values[table])
            if key is not None and key not in seen[table]:
                problems.append((table, ",".join(columns), "|".join(map(str, key)), f"{table} must_exist", "error"))
    return values, problems


def _key_columns(fields, row):
    """The natural key a mapping supplies for a table: its primary key, else its unique group."""
    pk = [c for c, p in fields.items() if p.get("primary_key") and c in row]
    return pk or [c for c, p in fields.items() if p.get("unique") and c in row]


def _key_is_blank(fields, row):
    """A destination row whose whole natural key has no value isn't created (P4)."""
    columns = _key_columns(fields, row)
    return bool(columns) and all(row[c] is None for c in columns)


def _primary_key(fields, row):
    pk = [c for c, p in fields.items() if p.get("primary_key")]
    if not all(row.get(c) is not None for c in pk):
        return None
    return tuple(row[c] for c in pk)


def _remember(schema, values, seen):
    """Rows that passed, so later files in the run can find them (must_exist)."""
    for table, row in values.items():
        key = _primary_key(schema[table], row)
        if key is not None:
            seen[table].add(key)


def _main(argv=None):
    parser = argparse.ArgumentParser(description="Validate source files against config/ (Milestone 2.1).")
    for name in ("cohort", "course", "cohort_config", "course_config", "enrichment", "enrichment_config"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--config_dir", default="config")
    parser.add_argument("--logs_dir", default="logs")
    parser.add_argument("--run_id")
    args = parser.parse_args(argv)
    try:
        summary = validate(**vars(args))
    except ConfigError as error:
        print(f"invalid config:\n{error}", file=sys.stderr)
        return 2
    except ValueError as error:
        parser.error(str(error))
    print(f"run id: {summary['run_id']}")
    for name, result in summary["results"].items():
        print(f"{name}: {result['rows_read']} rows read, {result['rows_passed']} passed, "
              f"{result['rows_failed']} failed")
    print(f"foreign keys: {summary['foreign_keys']}")
    print(f"report: {summary['report']}")
    print(f"failed records: {summary['failed_records'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
