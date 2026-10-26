# Normalized data model

This repository holds the contract for the normalized data model (GitHub issue #8 in `devcolor/pdp-datathon-starter`). It says:
- which tables exist, and each column's type and keys

Build against it now. The rest of the pipeline, which loads full data, comes later in Milestone 2.

## What's here

| File | What it is |
|---|---|
| `config/destination_schema.yaml` | The tables. One map per table; each column has its Python type (`str`, `int`, `float`, `bool`, `date`, `datetime`), whether it can be blank, and its keys. The only place types are defined. |
| `normalized-data-model.mmd` | The ER diagram, in Mermaid. A picture of `destination_schema.yaml`; a test keeps the two in step. |

## The tables

| Table | One row per |
|---|---|
| `institution` | institution. The mock data has one placeholder. |
| `data_provider` | loaded file, found again by its SHA-256 |
| `student` | student, with the demographics that don't change by cohort |
| `cohort_membership` | student and cohort, with everything the cohort file says about that cohort |
| `term` | academic year and term, plus one `UNKNOWN` term that sorts last |
| `course` | course prefix and number |
| `course_enrollment` | course-file row: a student in a course in a term |
| `student_attribute` | fact from an enrichment file about a student, optionally in a term |
| `institution_event` | event at an institution, such as a closure or a powwow |

## Rebuild and test

Run from this folder:

```bash
./scripts/test_config.sh                                 # schema and ER diagram
```

Requires Python 3 and the packages in `requirements.txt` (`pip install -r requirements.txt`).

IDs in comments, such as D6, P9 or SC-1, refer to decisions and success criteria in the Milestone 1 spec, kept in the team's planning notes.
