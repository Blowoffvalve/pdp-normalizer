"""Checks the mapping configs against the source files and the schema (Milestone 1 SC-1)."""

import csv
import hashlib
from pathlib import Path

import pytest
import yaml

BUILD = Path(__file__).resolve().parents[2]
CONFIG = BUILD / "config"
SCHEMA = yaml.safe_load(open(CONFIG / "destination_schema.yaml"))
ACTIONS = {"append", "replace", "keep"}
SOURCE_SIDE = {"format", "true_values", "false_values", "value_map", "missing_values", "unknown_values", "must_exist"}
# From data/manifest.json in devcolor/pdp-datathon-starter.
MANIFEST = {
    "pdp_cohort_data.csv": "85572e5a599281cfa17e430b8148c86adae246b8fc98ebbe128102a8f94aa4a6",
    "pdp_course_data.csv": "8884f40178ce845dc9a41269e2822250dab34d06c8ff3a78711b8905a8f733c1",
}
MAPPINGS = {
    "cohort_mapping.yaml": "data/input/pdp_cohort_data.csv",
    "course_mapping.yaml": "data/input/pdp_course_data.csv",
    "on_campus_housing_mapping.yaml": "data/input/enrichment/on_campus_housing.csv",
    "institution_events_mapping.yaml": "data/input/enrichment/institution_events.csv",
}


def load(name):
    return yaml.safe_load(open(CONFIG / name))


def header(path):
    with open(BUILD / path, newline="") as f:
        return next(csv.reader(f))


@pytest.mark.parametrize("name", MANIFEST)
def test_raw_input_files_match_the_starter_repo(name):
    digest = hashlib.sha256((BUILD / "data/input" / name).read_bytes()).hexdigest()
    assert digest == MANIFEST[name]


@pytest.mark.parametrize("config,source_file", MAPPINGS.items())
def test_every_source_column_is_mapped_or_dropped(config, source_file):
    sources = {e["source"] for e in load(config) if "source" in e}
    assert sources == set(header(source_file))


def test_analysis_ready_file_column_counts():
    assert len(header("data/input/pdp_cohort_data.csv")) == 85
    assert len(header("data/input/pdp_course_data.csv")) == 35


@pytest.mark.parametrize("config", MAPPINGS)
def test_entries_have_the_agreed_fields(config):
    for entry in load(config):
        if "dropped" in entry:
            assert set(entry) == {"source", "dropped"} and entry["dropped"]
            continue
        assert ("source" in entry) != ("value" in entry), entry
        required = {"destination_table", "destination_column", "on_conflict"}
        assert required <= set(entry) and set(entry) - {"source", "value"} - required <= SOURCE_SIDE, entry
        assert entry["on_conflict"] in ACTIONS, entry


@pytest.mark.parametrize("config", MAPPINGS)
def test_every_destination_exists_in_the_constants(config):
    for entry in load(config):
        if "dropped" not in entry:
            assert entry["destination_column"] in SCHEMA[entry["destination_table"]], entry


@pytest.mark.parametrize("config", MAPPINGS)
def test_a_destination_is_written_once_per_config(config):
    targets = [(e["destination_table"], e["destination_column"]) for e in load(config) if "dropped" not in e]
    assert len(targets) == len(set(targets))


def test_on_conflict_actions_from_the_spec():
    course = {(e["source"], e["destination_table"]): e["on_conflict"]
              for e in load("course_mapping.yaml") if "dropped" not in e}
    assert course[("Cohort_Term", "cohort_membership")] == "append"  # D1
    for column in ("Student_Age", "Race", "Ethnicity", "Gender"):
        assert course[(column, "student")] == "keep"  # D6, D7
    cohort = {(e["source"], e["destination_table"]): e["on_conflict"]
              for e in load("cohort_mapping.yaml") if "dropped" not in e}
    for column in ("Student_Age", "Race", "Ethnicity", "Gender", "First_Gen"):
        assert cohort[(column, "student")] == "replace"  # latest cohort row wins (D6)


def test_configs_carry_no_types():
    for config in MAPPINGS:
        assert all("data_type" not in e for e in load(config))  # D8
