"""Checks the destination schema and the ER diagram (Milestone 1 SC-2)."""

import re
from pathlib import Path

import yaml

BUILD = Path(__file__).resolve().parents[2]
SCHEMA = yaml.safe_load(open(BUILD / "config/destination_schema.yaml"))
TYPES = {"str", "int", "float", "bool", "date", "datetime"}


def test_constants_fields_have_a_known_type_and_nullability():
    for table, columns in SCHEMA.items():
        for column, props in columns.items():
            assert props["type"] in TYPES, (table, column)
            assert isinstance(props["nullable"], bool), (table, column)
            assert set(props) <= {"type", "nullable", "primary_key", "foreign_key", "unique",
                                  "default_value", "unknown_value", "list_delimiter", "rules"}, (table, column)


def test_constants_every_table_has_a_primary_key_and_keys_are_not_nullable():
    for table, columns in SCHEMA.items():
        pk = [c for c, p in columns.items() if p.get("primary_key")]
        assert pk, table
        assert not any(columns[c]["nullable"] for c in pk), table


def test_constants_foreign_keys_point_at_primary_keys():
    for table, columns in SCHEMA.items():
        for column, props in columns.items():
            if "foreign_key" in props:
                ref_table, ref_column = props["foreign_key"].split(".")
                assert SCHEMA[ref_table][ref_column].get("primary_key"), (table, column)
                assert SCHEMA[ref_table][ref_column]["type"] == props["type"], (table, column)


def test_er_diagram_matches_the_constants():
    mmd = (BUILD / "normalized-data-model.mmd").read_text()
    diagram = {}  # rules and defaults aren't drawn; only tables, columns and types
    for table, body in re.findall(r"\n  (\w+) \{\n(.*?)\n  \}", mmd, re.S):
        diagram[table] = [tuple(line.split()[:2])[::-1] for line in body.splitlines()]
    constants = {t: [(c, p["type"]) for c, p in cols.items()] for t, cols in SCHEMA.items()}
    assert diagram == constants
