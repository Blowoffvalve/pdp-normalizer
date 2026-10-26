"""Read, check and apply the validation rules (Milestone 2.1 spec, D2 and D4 to D7, P2 to P4).

Rules live in two places, split by what they describe (D2):
- config/destination_schema.yaml says what a valid stored value looks like: type, nullable, keys,
  default_value, unknown_value, list_delimiter and rules. config/value_sets.yaml holds the
  value lists those rules refer to by name.
- Each mapping config says how its source file writes values: format, true_values, false_values,
  value_map, missing_values, unknown_values and must_exist.

Public API:
- load_rules(config_dir, mapping_paths=None) reads and checks every file, or raises ConfigError.
- read_value(rules, entry, raw) turns one source value into a stored value and says which state
  it is in (D6): a value, a default (no value), unknown, or blank.
- check_value(rules, table, column, value, row=None, real=True) runs the column's rules on one
  stored value.
"""

import re
from datetime import date, datetime
from pathlib import Path

import yaml

__all__ = ["ConfigError", "load_rules", "read_value", "check_value", "VALUE", "DEFAULT", "UNKNOWN", "BLANK"]

_TYPES = {"str", "int", "float", "bool", "date", "datetime"}
_FIELD_KEYS = {"type", "nullable", "primary_key", "foreign_key", "unique",
               "default_value", "unknown_value", "list_delimiter", "rules"}
_ENTRY_KEYS = {"source", "value", "destination_table", "destination_column", "on_conflict",
               "format", "true_values", "false_values", "value_map", "missing_values",
               "unknown_values", "must_exist"}
_ACTIONS = {"append", "replace", "keep"}
_SEVERITIES = {"error", "warning"}
# Rule kinds (P2). Each rule has exactly one kind; the options go with it.
_KINDS = {"enum", "value_set", "pattern", "minimum", "maximum", "max_length",
          "not_before", "not_greater_than", "required_when", "academic_year"}
_OPTIONS = {"severity", "ignore_case", "ignore_spaces", "when"}
_RANGE = {"minimum", "maximum"}  # the one pair of kinds that may share a rule

# States of a value read from a source file (D5, D6).
VALUE, DEFAULT, UNKNOWN, BLANK = "value", "default", "unknown", "blank"


class ConfigError(ValueError):
    """A config file breaks the rule format. The validator stops before reading any data."""


def load_rules(config_dir="config", mapping_paths=None):
    """Read the schema, the value sets and the mapping configs, and check them all.

    mapping_paths defaults to every *_mapping.yaml in config_dir. Returns
    {"schema": ..., "value_sets": ..., "mappings": {file name: entries}, "files": [paths]}.
    """
    config_dir = Path(config_dir)
    schema_path, sets_path = config_dir / "destination_schema.yaml", config_dir / "value_sets.yaml"
    schema = _load_yaml(schema_path)
    value_sets = _load_yaml(sets_path) if sets_path.exists() else {}
    paths = [Path(p) for p in mapping_paths] if mapping_paths else sorted(config_dir.glob("*_mapping.yaml"))
    mappings = {p.name: _load_yaml(p) for p in paths}
    problems = _check_value_sets(value_sets) + _check_schema(schema, value_sets)
    for name, entries in mappings.items():
        problems += _check_mapping(name, entries, schema)
    if problems:
        raise ConfigError("\n".join(problems))
    files = [schema_path] + ([sets_path] if sets_path.exists() else []) + paths
    return {"schema": schema, "value_sets": value_sets, "mappings": mappings, "files": files}


def read_value(rules, entry, raw):
    """One source value to its stored value (P4 step 1, D5 to D7).

    Returns (state, value, problem). problem is None, or a short reason the value couldn't be
    converted to the column's type, in which case value is None.
    """
    field = rules["schema"][entry["destination_table"]][entry["destination_column"]]
    if "value" in entry:
        raw = str(entry["value"])
    raw = entry.get("value_map", {}).get(raw, raw)
    if raw == "" or raw in [str(v) for v in entry.get("missing_values", [])]:
        if "default_value" in field:
            return DEFAULT, field["default_value"], None
        return BLANK, None, None
    if raw in [str(v) for v in entry.get("unknown_values", [])]:
        if "unknown_value" in field:
            return UNKNOWN, field["unknown_value"], None
        return UNKNOWN, None, None
    try:
        return VALUE, _convert(raw, field["type"], entry), None
    except (ValueError, KeyError):
        return VALUE, None, f"not a {field['type']}"


def check_value(rules, table, column, value, row=None, real=True):
    """Run the column's rules on one stored value (P4 step 3).

    row is {table: {column: value}} for the same source row, for the two-column rules. Rules
    only check real values: for a blank, default or unknown value pass real=False, and only
    required_when runs. Returns a list of (rule name, severity) for the rules that failed.
    """
    field = rules["schema"][table][column]
    row = row or {}
    failed = []
    for index, rule in enumerate(field.get("rules", [])):
        if "when" in rule and not _condition(rule["when"], table, row):
            continue
        kinds = [k for k in rule if k in _KINDS]
        kind = kinds[0]
        name = f"{table}.{column}#{index} {'+'.join(kinds)}"
        if kind == "required_when":
            if value is None and _condition(rule["required_when"], table, row):
                failed.append((name, rule["severity"]))
            continue
        if value is None or not real:
            continue
        items = value.split(field["list_delimiter"]) if field.get("list_delimiter") else [value]
        if not all(_passes(rules, rule, kind, item, table, row) for item in items):
            failed.append((name, rule["severity"]))
    return failed


def _load_yaml(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"{path}: {error}") from error


def _convert(raw, type_name, entry):
    if type_name == "str":
        return raw
    if type_name == "int":
        return int(raw)
    if type_name == "float":
        return float(raw)
    if type_name == "bool":
        if raw in [str(v) for v in entry["true_values"]]:
            return True
        if raw in [str(v) for v in entry["false_values"]]:
            return False
        raise ValueError(raw)
    if type_name == "date":
        return datetime.strptime(raw, entry["format"]).date()
    return datetime.fromisoformat(raw)


def _condition(condition, table, row):
    other_table, other_column = _split_column(condition["column"], table)
    other = row.get(other_table, {}).get(other_column)
    if condition.get("present"):
        return other is not None
    return other is not None and str(other) == str(condition["equals"])


def _split_column(name, table):
    return tuple(name.split(".", 1)) if "." in name else (table, name)


def _normalize(value, rule):
    text = str(value)
    if rule.get("ignore_spaces"):
        text = re.sub(r"\s+", "", text)
    if rule.get("ignore_case"):
        text = text.casefold()
    return text


def _passes(rules, rule, kind, value, table, row):
    if kind in ("enum", "value_set"):
        allowed = rule["enum"] if kind == "enum" else rules["value_sets"][rule["value_set"]]
        return _normalize(value, rule) in {_normalize(v, rule) for v in allowed}
    if kind == "pattern":
        return re.fullmatch(rule["pattern"], str(value)) is not None
    if kind in _RANGE:
        low, high = rule.get("minimum"), rule.get("maximum")
        return (low is None or value >= low) and (high is None or value <= high)
    if kind == "max_length":
        return len(str(value)) <= rule["max_length"]
    if kind == "academic_year":
        match = re.fullmatch(r"([0-9]{4})-([0-9]{2})", str(value))
        return bool(match) and (int(match.group(1)) + 1) % 100 == int(match.group(2))
    other_table, other_column = _split_column(rule[kind], table)
    other = row.get(other_table, {}).get(other_column)
    if other is None:
        return True  # nothing to compare against
    try:
        return value >= other if kind == "not_before" else value <= other
    except TypeError:
        return True  # the other column holds an unknown or default, not a comparable value


def _check_value_sets(value_sets):
    return [f"value_sets.yaml: {name} must be a non-empty list"
            for name, values in value_sets.items() if not isinstance(values, list) or not values]


def _check_schema(schema, value_sets):
    problems = []
    for table, columns in schema.items():
        for column, field in columns.items():
            where = f"destination_schema.yaml: {table}.{column}"
            unknown_keys = set(field) - _FIELD_KEYS
            if unknown_keys:
                problems.append(f"{where}: unknown properties {sorted(unknown_keys)}")
            if field.get("type") not in _TYPES:
                problems.append(f"{where}: type must be one of {sorted(_TYPES)}")
                continue
            for key in ("default_value", "unknown_value"):
                if key in field and not _fits_type(field[key], field["type"]):
                    problems.append(f"{where}: {key} {field[key]!r} is not a {field['type']}")
            for index, rule in enumerate(field.get("rules", [])):
                problems += _check_rule(f"{where} rule {index}", rule, table, schema, value_sets)
    return problems


def _fits_type(value, type_name):
    expected = {"str": str, "int": int, "float": (int, float), "bool": bool,
                "date": date, "datetime": datetime}[type_name]
    return isinstance(value, expected) and not (type_name in ("int", "float") and isinstance(value, bool))


def _check_rule(where, rule, table, schema, value_sets):
    if not isinstance(rule, dict):
        return [f"{where}: a rule must be a map, not {rule!r}"]
    problems = []
    kinds = [k for k in rule if k in _KINDS]
    unknown_keys = set(rule) - _KINDS - _OPTIONS
    if unknown_keys:
        problems.append(f"{where}: unknown rule kind {sorted(unknown_keys)}")
    if not kinds or (len(kinds) > 1 and not set(kinds) <= _RANGE):
        problems.append(f"{where}: needs exactly one rule kind (minimum and maximum may share one)")
    if rule.get("severity") not in _SEVERITIES:
        problems.append(f"{where}: severity must be error or warning")
    if "value_set" in rule and rule["value_set"] not in value_sets:
        problems.append(f"{where}: no value set named {rule['value_set']!r}")
    if "pattern" in rule:
        try:
            re.compile(rule["pattern"])
        except re.error as error:
            problems.append(f"{where}: bad pattern ({error})")
    for key in ("not_before", "not_greater_than"):
        if key in rule and not _column_exists(rule[key], table, schema):
            problems.append(f"{where}: no column {rule[key]!r}")
    for key in ("required_when", "when"):
        if key in rule:
            condition = rule[key]
            if not isinstance(condition, dict) or "column" not in condition or \
                    len({"equals", "present"} & set(condition)) != 1:
                problems.append(f"{where}: {key} needs a column and either equals or present")
            elif not _column_exists(condition["column"], table, schema):
                problems.append(f"{where}: no column {condition['column']!r}")
    return problems


def _column_exists(name, table, schema):
    other_table, other_column = _split_column(name, table)
    return other_column in schema.get(other_table, {})


def _check_mapping(name, entries, schema):
    problems = []
    for number, entry in enumerate(entries, start=1):
        where = f"{name} entry {number} ({entry.get('source', entry.get('value'))})"
        if "dropped" in entry:
            if set(entry) != {"source", "dropped"} or not entry["dropped"]:
                problems.append(f"{where}: a dropped entry has only source and dropped (the reason)")
            continue
        unknown_keys = set(entry) - _ENTRY_KEYS
        if unknown_keys:
            problems.append(f"{where}: unknown properties {sorted(unknown_keys)}")
        if ("source" in entry) == ("value" in entry):
            problems.append(f"{where}: needs either source or value")
        if entry.get("on_conflict") not in _ACTIONS:
            problems.append(f"{where}: on_conflict must be one of {sorted(_ACTIONS)}")
        field = schema.get(entry.get("destination_table"), {}).get(entry.get("destination_column"))
        if field is None:
            problems.append(f"{where}: no destination {entry.get('destination_table')}.{entry.get('destination_column')}")
            continue
        if field.get("type") == "date" and "format" not in entry:
            problems.append(f"{where}: a date column needs format")
        if field.get("type") == "bool" and not ("true_values" in entry and "false_values" in entry):
            problems.append(f"{where}: a true/false column needs true_values and false_values")
        if entry.get("unknown_values") and field.get("type") == "str" and "unknown_value" not in field:
            problems.append(f"{where}: unknown_values on a text column whose schema has no unknown_value (D6)")
        if "value_map" in entry and not isinstance(entry["value_map"], dict):
            problems.append(f"{where}: value_map must map source values to repaired values")
    return problems
