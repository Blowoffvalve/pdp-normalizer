# Normalized data model: mapping configs, schema and samples

This folder holds the contract for the normalized data model (GitHub issue #8 in `devcolor/pdp-datathon-starter`). It says which tables exist, what type each column is, and how every column of the mock Analysis-Ready Files lands in a table. Build against it now; the pipeline that loads full data through it comes in Milestone 2.

**The sample rows are real rows from the mock data, but they have not been validated.** The schema and the configs are the stable part. Values in the samples can be wrong in the same ways the mock data is.

## What's here

| File | What it is |
|---|---|
| `config/destination_schema.yaml` | The tables. One map per table; each column has its Python type (`str`, `int`, `float`, `bool`, `date`, `datetime`), whether it can be blank, and its keys. The only place types are defined. |
| `config/cohort_mapping.yaml` | Where each of the 85 cohort-file columns goes. |
| `config/course_mapping.yaml` | Where each of the 35 course-file columns goes. |
| `config/on_campus_housing_mapping.yaml` | Where the on-campus housing enrichment goes (`student_attribute`). |
| `config/institution_events_mapping.yaml` | Where the institution events enrichment goes (`institution_event`). |
| `normalized-data-model.mmd` | The ER diagram, in Mermaid. A picture of `destination_schema.yaml`; a test keeps the two in step. |
| `data/input/` | The mock Analysis-Ready Files (copies of the starter repo's `data/raw/`, checked by checksum) and the two enrichment files. |
| `data/input/sample/` | The raw rows for the 10 sample students, in the same formats. |
| `data/output/sample/` | One CSV per table, built from `data/input/sample/` through the configs. |
| `src/sample/build_sample.py` | Rebuilds both sample folders. |
| `src/generator/enrichment/on_campus_housing.py` | Generates the synthetic on-campus housing file. |

## How a column moves from a file to a table

Each mapping config is a list of entries, one per source column:

```yaml
- source: Course_Begin_Date            # column in the course file
  destination_table: course_enrollment
  destination_column: course_begin_date
  validation_rule: Format YYYYMMDD; '.' means blank
  on_conflict: replace
```

1. The value is read as text, exactly as it is in the file.
2. It's converted to the type in `destination_schema.yaml`. Here that's `date`, so `20240319` becomes `2024-03-19`. For non-text types, `.`, `UK`, `MISSING` and blank all mean "no value".
3. It's written to `course_enrollment.course_begin_date`.

The other parts of an entry:
- **`validation_rule`** says what a valid value looks like, taken from the starter repo's data dictionaries. Milestone 2's validator enforces it; nothing rejects rows yet.
- **`on_conflict`** says what happens when the row already exists and already holds a value: `replace` overwrites it, `keep` leaves it, `append` keeps it and adds the new value. For example, the course file repeats each student's gender, which the cohort file already wrote, so that entry is `keep`.
- **`value:`** replaces `source:` when every row gets the same fixed value, such as `attribute_key: uses_on_campus_housing`.
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
- **The mock data has no institution column.** Everything belongs to one placeholder institution, and the two example events come from two real colleges: Leech Lake Tribal College and United Tribes Technical College.
- **On-campus housing is synthetic.** 30% of students, a 60% chance of staying each term, generated with a fixed seed.

## Rebuild and test

Run from this folder:

```bash
python3 -m src.generator.enrichment.on_campus_housing   # data/input/enrichment/on_campus_housing.csv
python3 -m src.sample.build_sample                       # data/input/sample/ and data/output/sample/
./scripts/test_config.sh                                 # configs, schema and ER diagram
./scripts/test_sample.sh                                 # sample builder and samples
./scripts/test_generator.sh                              # on-campus housing generator
```

Requires Python 3 and the packages in `requirements.txt` (`pip install -r requirements.txt`).

IDs in comments, such as D6, P9 or SC-1, refer to decisions and success criteria in the Milestone 1 spec, kept in the team's planning notes.
