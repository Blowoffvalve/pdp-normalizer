"""Checks the rule format in the configs (Milestone 2.1 SC-1 to SC-8, SC-14)."""

from pathlib import Path

import pytest
import yaml

from src.rules.rules import load_rules

BUILD = Path(__file__).resolve().parents[2]
CONFIG = BUILD / "config"
SCHEMA = yaml.safe_load(open(CONFIG / "destination_schema.yaml"))
MAPPINGS = sorted(p.name for p in CONFIG.glob("*_mapping.yaml"))
STORED_SIDE = {"rules", "default_value", "unknown_value", "list_delimiter", "type", "nullable"}


def load(name):
    return yaml.safe_load(open(CONFIG / name))


def test_the_configs_pass_the_rule_format_check():  # SC-1, SC-7
    load_rules(CONFIG)


@pytest.mark.parametrize("config", MAPPINGS)
def test_no_free_text_rules_and_no_stored_value_rules_in_mappings(config):  # SC-1, SC-2
    for entry in load(config):
        assert "validation_rule" not in entry, entry
        assert not set(entry) & STORED_SIDE, entry


def test_no_rule_restates_the_schema():  # SC-3
    restated = {"required", "nullable", "type", "unique", "foreign_key", "primary_key"}
    for table, columns in SCHEMA.items():
        for column, props in columns.items():
            for rule in props.get("rules", []):
                assert not set(rule) & restated, (table, column, rule)


def test_a_list_used_twice_is_a_value_set():  # SC-4
    seen = {}
    for table, columns in SCHEMA.items():
        for column, props in columns.items():
            for rule in props.get("rules", []):
                if "enum" in rule:
                    key = tuple(rule["enum"])
                    assert key not in seen, f"{table}.{column} repeats the list of {seen.get(key)}"
                    seen[key] = f"{table}.{column}"
    sets = load("value_sets.yaml")
    used = {rule["value_set"] for cols in SCHEMA.values() for p in cols.values() for rule in p.get("rules", [])
            if "value_set" in rule}
    assert used == set(sets)  # no value set is unused


def test_unknown_values_follow_the_type_convention():  # SC-5, D6
    for table, columns in SCHEMA.items():
        for column, props in columns.items():
            if props["type"] == "str":
                assert props.get("unknown_value", "UNKNOWN") == "UNKNOWN", (table, column)
            else:
                assert "unknown_value" not in props, (table, column)


def test_blank_term_goes_to_the_unknown_term():  # SC-5
    assert SCHEMA["term"]["academic_year"]["default_value"] == "UNKNOWN"
    assert SCHEMA["term"]["academic_term"]["default_value"] == "UNKNOWN"


def test_two_column_rules_are_data():  # SC-6
    rules = lambda t, c: SCHEMA[t][c]["rules"]
    assert {"not_before": "course_begin_date", "severity": "error"} in rules("course_enrollment", "course_end_date")
    assert {"not_greater_than": "number_of_credits_attempted", "severity": "error"} in \
        rules("course_enrollment", "number_of_credits_earned")
    assert {"required_when": {"column": "core_course", "equals": "Y"}, "severity": "error"} in \
        rules("course_enrollment", "core_course_type")


def test_cut_race_labels_are_repaired_in_both_files():  # SC-14, D7
    for config in ("cohort_mapping.yaml", "course_mapping.yaml"):
        race = next(e for e in load(config) if e.get("source") == "Race")
        assert race["value_map"]["American Indian or Alaska"] == "American Indian or Alaska Native"
        assert race["value_map"]["Native Hawaiian or Other"] == "Native Hawaiian or Other Pacific Islander"


@pytest.mark.parametrize("name", ["destination_schema.yaml", *MAPPINGS])
def test_each_file_says_what_belongs_in_it(name):  # SC-8
    header = (CONFIG / name).read_text().split("\n\n")[0]
    other = "mapping config" if name == "destination_schema.yaml" else "config/destination_schema.yaml"
    assert other in header, name
    assert "what a valid stored value looks like" in header.lower(), name
