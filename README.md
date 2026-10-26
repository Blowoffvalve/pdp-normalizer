# Normalized data model

This repository holds the contract for the normalized data model (GitHub issue #8 in `devcolor/pdp-datathon-starter`). It says:
- which tables exist, and each column's type and keys
- where every column of the two mock Analysis-Ready Files goes

Build against it now. The rest of the pipeline, which loads full data, comes later in Milestone 2.

## What's here

| File | What it is |
|---|---|
| `config/destination_schema.yaml` | The tables. One map per table; each column has its Python type (`str`, `int`, `float`, `bool`, `date`, `datetime`), whether it can be blank, and its keys. The only place types are defined. |
| `config/cohort_mapping.yaml` | Where each of the 85 cohort-file columns goes. |
| `config/course_mapping.yaml` | Where each of the 35 course-file columns goes. |
| `normalized-data-model.mmd` | The ER diagram, in Mermaid. A picture of `destination_schema.yaml`; a test keeps the two in step. |
| `data/input/` | The mock Analysis-Ready Files (copies of the starter repo's `data/raw/`, checked by checksum). |

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

## How a column moves from a file to a table

Each mapping config is a list of entries, one per source column. Follow `Number_of_Credits_Earned` from the course file:

```yaml
- source: Number_of_Credits_Earned     # column in the course file
  destination_table: course_enrollment
  destination_column: number_of_credits_earned
  on_conflict: replace
```

1. The value is read as text, exactly as it is in the file.
2. It's converted to the type in `destination_schema.yaml`, here `float`, so `3` becomes `3.0`.
3. It's written to `course_enrollment.number_of_credits_earned`.

What counts as "no value" (the course file writes `.`) and what a valid value is come in the next layer.

The other parts of a mapping entry:
- **`on_conflict`** says what happens when the row already exists and already holds a value: `replace` overwrites it, `keep` leaves it, `append` keeps it and adds the new value. For example, the course file repeats each student's gender, which the cohort file already wrote, so that entry is `keep`.
- **`dropped:`** marks a source column with no destination, with the reason (only `researchid` today).

**Keys and links between tables.** A source row can fill several tables at once. A course-file row finds its `term` and `course`, then writes a `course_enrollment` row that points at them. Rows are found by their natural key:
- `student`: `student_id`
- `cohort_membership`: student and cohort
- `term`: year and term
- `course`: prefix and number
- `course_enrollment`: the file plus its row number

## Things to know before you query

- **Join cohort data on student and cohort, not student alone.** 2 students are in two cohorts.
- **17,755 course-file rows have no course record.** On those rows the student was enrolled at another institution that term, or the term is unknown. They're kept, with a blank `course_id`, `section_id` and `attempt_number`.
- **456 course rows have no year or term.** They point at the one `UNKNOWN` term, which sorts last.
- **`attempt_number` counts retakes.** It's the order of a student's rows for the same course, by term.
- **The course file's `Cohort_Term` often disagrees with the cohort file.** The cohort file's value is kept. Why it disagrees is an open question for Dena (/dev/color).
- **The mock data has no institution column.** Everything belongs to one placeholder institution.

## Rebuild and test

Run from this folder:

```bash
./scripts/test_config.sh                                 # schema, mapping configs and ER diagram
```

Requires Python 3 and the packages in `requirements.txt` (`pip install -r requirements.txt`).

IDs in comments, such as D6, P9 or SC-1, refer to decisions and success criteria in the Milestone 1 spec, kept in the team's planning notes.
