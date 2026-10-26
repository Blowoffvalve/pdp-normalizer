"""Checks the rules module: reading and checking configs, reading values, running rules (Milestone 2.1 SC-1 to SC-7, SC-14)."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from src.rules import rules as r
from src.rules.rules import ConfigError, check_value, load_rules, read_value

BUILD = Path(__file__).resolve().parents[2]
RULES = load_rules(BUILD / "config")


def write_configs(tmp_path, schema, mapping=None, value_sets=None):
    (tmp_path / "destination_schema.yaml").write_text(yaml.safe_dump(schema))
    if value_sets is not None:
        (tmp_path / "value_sets.yaml").write_text(yaml.safe_dump(value_sets))
    if mapping is not None:
        (tmp_path / "test_mapping.yaml").write_text(yaml.safe_dump(mapping))
    return tmp_path


def small(rules=None, **field):
    """Rules for one table t with columns a (the one under test), b and c."""
    a = dict({"type": "str", "nullable": True}, **field)
    if rules:
        a["rules"] = rules
    schema = {"t": {"a": a, "b": {"type": "str", "nullable": True}, "c": {"type": "float", "nullable": True}}}
    return {"schema": schema, "value_sets": {"yn": ["Y", "N"]}, "mappings": {}, "files": []}


def entry(**fields):
    return dict({"source": "A", "destination_table": "t", "destination_column": "a", "on_conflict": "replace"}, **fields)


# load_rules

def test_the_real_configs_load():
    assert {"cohort_mapping.yaml", "course_mapping.yaml"} <= set(RULES["mappings"])
    assert [p.name for p in RULES["files"][:2]] == ["destination_schema.yaml", "value_sets.yaml"]


def test_load_rules_reads_only_the_given_mappings():
    loaded = load_rules(BUILD / "config", [BUILD / "config/course_mapping.yaml"])
    assert list(loaded["mappings"]) == ["course_mapping.yaml"]


@pytest.mark.parametrize("rule,message", [
    ({"enumm": ["a"], "severity": "error"}, "unknown rule kind"),
    ({"enum": ["a"]}, "severity"),
    ({"enum": ["a"], "severity": "fatal"}, "severity"),
    ({"enum": ["a"], "pattern": "x", "severity": "error"}, "exactly one rule kind"),
    ({"value_set": "nope", "severity": "error"}, "no value set"),
    ({"pattern": "(", "severity": "error"}, "bad pattern"),
    ({"not_before": "missing", "severity": "error"}, "no column"),
    ({"required_when": {"column": "b"}, "severity": "error"}, "either equals or present"),
    ({"required_when": {"column": "x.y", "present": True}, "severity": "error"}, "no column"),
    ("Required; AllowedValues [a]", "must be a map"),
])
def test_load_rules_rejects_a_bad_rule(tmp_path, rule, message):
    schema = {"t": {"a": {"type": "str", "nullable": True, "rules": [rule]}, "b": {"type": "str", "nullable": True}}}
    with pytest.raises(ConfigError, match=message):
        load_rules(write_configs(tmp_path, schema, value_sets={"yn": ["Y", "N"]}))


def test_minimum_and_maximum_may_share_a_rule(tmp_path):
    schema = {"t": {"a": {"type": "int", "nullable": True, "rules": [{"minimum": 0, "maximum": 4, "severity": "error"}]}}}
    load_rules(write_configs(tmp_path, schema))


@pytest.mark.parametrize("field,message", [
    ({"type": "text", "nullable": True}, "type must be one of"),
    ({"type": "str", "nullable": True, "colour": "red"}, "unknown properties"),
    ({"type": "int", "nullable": True, "default_value": "UNKNOWN"}, "default_value"),
    ({"type": "float", "nullable": True, "unknown_value": True}, "unknown_value"),
])
def test_load_rules_rejects_a_bad_field(tmp_path, field, message):
    with pytest.raises(ConfigError, match=message):
        load_rules(write_configs(tmp_path, {"t": {"a": field}}))


@pytest.mark.parametrize("field,extra,message", [
    ({"type": "str", "nullable": True}, {"validation_rule": "Required"}, "unknown properties"),
    ({"type": "date", "nullable": True}, {}, "needs format"),
    ({"type": "bool", "nullable": True}, {"true_values": ["1"]}, "true_values and false_values"),
    ({"type": "str", "nullable": True}, {"unknown_values": ["UK"]}, "no unknown_value"),
    ({"type": "str", "nullable": True}, {"on_conflict": "merge"}, "on_conflict"),
    ({"type": "str", "nullable": True}, {"destination_column": "zzz"}, "no destination"),
])
def test_load_rules_rejects_a_bad_mapping_entry(tmp_path, field, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_rules(write_configs(tmp_path, {"t": {"a": field}}, [entry(**extra)]))


def test_load_rules_reports_a_file_that_is_not_yaml(tmp_path):
    (tmp_path / "destination_schema.yaml").write_text("t: {a: [")
    with pytest.raises(ConfigError):
        load_rules(tmp_path)


# read_value

def test_read_value_states():
    rules = small(default_value="NONE", unknown_value="UNKNOWN")
    e = entry(missing_values=["."], unknown_values=["UK"], value_map={"Alask": "Alaska"})
    assert read_value(rules, e, "x") == (r.VALUE, "x", None)
    assert read_value(rules, e, "Alask") == (r.VALUE, "Alaska", None)  # repaired (D7)
    assert read_value(rules, e, ".") == (r.DEFAULT, "NONE", None)
    assert read_value(rules, e, "") == (r.DEFAULT, "NONE", None)
    assert read_value(rules, e, "UK") == (r.UNKNOWN, "UNKNOWN", None)


def test_read_value_without_default_or_unknown_value_is_blank():
    rules = small(type="float")
    e = entry(missing_values=["."], unknown_values=["UK"])
    assert read_value(rules, e, ".") == (r.BLANK, None, None)
    assert read_value(rules, e, "UK") == (r.UNKNOWN, None, None)  # numbers store unknown as blank (D6)


def test_read_value_converts_to_the_type():
    assert read_value(small(type="int"), entry(), "3") == (r.VALUE, 3, None)
    assert read_value(small(type="float"), entry(), "2.5") == (r.VALUE, 2.5, None)
    assert read_value(small(type="date"), entry(format="%Y%m%d"), "20240319") == (r.VALUE, date(2024, 3, 19), None)
    assert read_value(small(type="bool"), entry(true_values=["1"], false_values=["0"]), "0") == (r.VALUE, False, None)
    assert read_value(small(type="int"), entry(value=7), "") == (r.VALUE, 7, None)


@pytest.mark.parametrize("type_name,extra,raw", [
    ("int", {}, "abc"), ("float", {}, "x"), ("date", {"format": "%Y%m%d"}, "2024-03-19"),
    ("bool", {"true_values": ["1"], "false_values": ["0"]}, "yes"),
])
def test_read_value_reports_a_value_that_is_not_its_type(type_name, extra, raw):
    assert read_value(small(type=type_name), entry(**extra), raw) == (r.VALUE, None, f"not a {type_name}")


# check_value

def failed(rule, value, row=None, real=True, **field):
    return check_value(small([dict(rule, severity="error")], **field), "t", "a", value, row, real=real)


@pytest.mark.parametrize("rule,good,bad", [
    ({"enum": ["Fall", "Spring"]}, "Fall", "fall"),
    ({"enum": ["Fall"], "ignore_case": True}, "FALL", "Winter"),
    ({"enum": ["> 20-24"], "ignore_spaces": True}, ">20 - 24", "> 25"),
    ({"value_set": "yn"}, "Y", "U"),
    ({"pattern": "^[0-9]{6}$"}, "240101", "24.0101"),
    ({"max_length": 3}, "abc", "abcd"),
    ({"academic_year": True}, "2023-24", "2023-25"),
    ({"academic_year": True}, "1999-00", "2023"),
])
def test_single_column_kinds(rule, good, bad):
    assert failed(rule, good) == []
    assert failed(rule, bad) == [(f"t.a#0 {next(iter(rule))}", "error")]


def test_range_kinds_on_numbers():
    rules = {"schema": {"t": {"a": {"type": "float", "nullable": True,
                                    "rules": [{"minimum": 0, "maximum": 20, "severity": "warning"}]}}}}
    assert check_value(rules, "t", "a", 20.0) == []
    assert check_value(rules, "t", "a", 20.5) == [("t.a#0 minimum+maximum", "warning")]
    assert check_value(rules, "t", "a", -1.0) == [("t.a#0 minimum+maximum", "warning")]


def test_two_column_kinds():
    assert failed({"not_greater_than": "c"}, 3.0, {"t": {"c": 4.0}}) == []
    assert failed({"not_greater_than": "c"}, 5.0, {"t": {"c": 4.0}}) != []
    assert failed({"not_greater_than": "c"}, 5.0, {"t": {"c": None}}) == []  # nothing to compare
    assert failed({"not_before": "b"}, date(2024, 1, 2), {"t": {"b": date(2024, 1, 1)}}) == []
    assert failed({"not_before": "b"}, date(2023, 1, 1), {"t": {"b": date(2024, 1, 1)}}) != []
    assert failed({"not_before": "b"}, date(2023, 1, 1), {"t": {"b": "UNKNOWN"}}) == []


def test_required_when():
    rule = {"required_when": {"column": "b", "equals": "Y"}}
    assert failed(rule, None, {"t": {"b": "Y"}}) != []
    assert failed(rule, None, {"t": {"b": "N"}}) == []
    assert failed(rule, "Humanities", {"t": {"b": "Y"}}) == []
    present = {"required_when": {"column": "u.p", "present": True}}
    assert failed(present, None, {"u": {"p": "ENG"}}) != []
    assert failed(present, None, {"u": {"p": None}}) == []
    assert failed(present, "UNKNOWN", {"u": {"p": "ENG"}}, real=False) == []  # unknown is a value


def test_when_limits_a_rule_to_some_rows():
    rule = {"enum": ["true", "false"], "when": {"column": "b", "equals": "uses_on_campus_housing"}}
    assert failed(rule, "maybe", {"t": {"b": "uses_on_campus_housing"}}) != []
    assert failed(rule, "maybe", {"t": {"b": "tribal_affiliation"}}) == []


def test_rules_skip_values_that_are_not_real():
    assert failed({"enum": ["Y"]}, None) == []
    assert failed({"enum": ["Y"]}, "UNKNOWN", real=False) == []


def test_list_delimiter_checks_each_item():
    rule = {"pattern": "^[A-Z]{2}$"}
    assert failed(rule, "MN|ND", list_delimiter="|") == []
    assert failed(rule, "MN|North Dakota", list_delimiter="|") != []
