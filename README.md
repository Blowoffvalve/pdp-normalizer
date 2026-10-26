# Normalized data model

This repository holds the contract for the normalized data model (GitHub issue #8 in `devcolor/pdp-datathon-starter`). It says:
- which tables exist, and each column's type and keys
- where every column of the two mock Analysis-Ready Files goes
- what a valid stored value looks like

Build against it now. The rest of the pipeline, which loads full data, comes later in Milestone 2.

## What's here

| File | What it is |
|---|---|
| `config/destination_schema.yaml` | The tables, and what a valid stored value looks like. One map per table; each column has its Python type (`str`, `int`, `float`, `bool`, `date`, `datetime`), whether it can be blank, its keys, and its validation rules. The only place types and rules are defined. |
| `config/value_sets.yaml` | Value lists that several columns share, such as `yes_no` and `academic_term`. Rules refer to them by name. |
| `config/cohort_mapping.yaml` | Where each of the 85 cohort-file columns goes. |
| `config/course_mapping.yaml` | Where each of the 35 course-file columns goes. |
| `normalized-data-model.mmd` | The ER diagram, in Mermaid. A picture of `destination_schema.yaml`; a test keeps the two in step. |
| `data/input/` | The mock Analysis-Ready Files (copies of the starter repo's `data/raw/`, checked by checksum). |
| `src/rules/rules.py` | Reads and checks the configs, and applies them to one value. |

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

## Two kinds of config, split by what they describe

| File | Says | Examples |
|---|---|---|
| `config/destination_schema.yaml` | **What a valid stored value looks like**, whichever file it came from. | type, can it be blank, keys, `default_value`, `unknown_value`, `rules` |
| Each mapping config (`config/*_mapping.yaml`) | **How one source file writes its values**, and where each column goes. | `format`, `missing_values`, `unknown_values`, `value_map`, `true_values`, `on_conflict` |

So a squad adding an enrichment file writes only a mapping config, and touches `destination_schema.yaml` only to add a column. Two files that write the same column can't disagree about what's valid, because the rule is written once.

## How a column moves from a file to a table

Follow `Number_of_Credits_Earned` from the course file:

```yaml
# config/course_mapping.yaml: how the course file writes it
- source: Number_of_Credits_Earned
  destination_table: course_enrollment
  destination_column: number_of_credits_earned
  missing_values: [.]          # this file writes "." for no value
  on_conflict: replace

# config/destination_schema.yaml: what a valid stored value is
course_enrollment:
  number_of_credits_earned:
    type: float
    nullable: true
    rules:
      - {minimum: 0, maximum: 20, severity: error}
      - {not_greater_than: number_of_credits_attempted, severity: error}
```

1. **Repair.** If the entry has a `value_map` and the value is in it, it's replaced by the repaired value. For example, `Race` repairs `American Indian or Alaska` (cut at 25 characters in the mock files) to `American Indian or Alaska Native`.
2. **No value or unknown.**
   - A blank cell, or a code in `missing_values`, means **no value**. The column's `default_value` is stored, or blank.
   - A code in `unknown_values` means the file **doesn't know**. The column's `unknown_value` is stored: `UNKNOWN` for text columns, blank for numbers, dates and true/false columns.
3. **Type.** Anything else is converted to the schema's type, using `format` for dates and `true_values`/`false_values` for true/false. Here `3` becomes `3.0`. A value that won't convert fails.
4. **Rules.** The column's rules check real values only, not blanks, defaults or unknowns.
5. The value is written to `course_enrollment.number_of_credits_earned`.

The other parts of a mapping entry:
- **`on_conflict`** says what happens when the row already exists and already holds a value: `replace` overwrites it, `keep` leaves it, `append` keeps it and adds the new value. For example, the course file repeats each student's gender, which the cohort file already wrote, so that entry is `keep`.
- **`must_exist: true`** means the row must already have been written by another file in the same run. The course file's student and cohort must be in the cohort file.
- **`dropped:`** marks a source column with no destination, with the reason (only `researchid` today).

## Rule kinds

Each rule is one YAML map with one kind and a `severity`: `error` holds the row back, `warning` reports it and still loads it. Names follow [Frictionless Table Schema](https://specs.frictionlessdata.io/table-schema/) where it has one.

| Kind | Checks | Example |
|---|---|---|
| `enum` | one of a list | `{enum: [F, O, H], severity: error}` |
| `value_set` | one of a list in `config/value_sets.yaml` | `{value_set: yes_no, severity: error}` |
| `pattern` | the text matches a regular expression | `{pattern: '^[0-9]{6}$', severity: error}` |
| `minimum`, `maximum` | a number is in range (one rule can have both) | `{minimum: 0, maximum: 20, severity: error}` |
| `max_length` | text is at most this long | `{max_length: 20, severity: error}` |
| `not_before` | a date is on or after another column's | `{not_before: course_begin_date, severity: error}` |
| `not_greater_than` | a number is at most another column's | `{not_greater_than: number_of_credits_attempted, severity: error}` |
| `required_when` | the column has a value when another column equals a value, or has one | `{required_when: {column: core_course, equals: Y}, severity: error}` |
| `academic_year` | `YYYY-YY`, where the second year follows the first | `{academic_year: true, severity: error}` |

Options:
- `enum` and `value_set` take `ignore_case: true` and `ignore_spaces: true`.
- Any rule takes `when: {column: ..., equals: ...}` to apply only to some rows. `student_attribute.attribute_value` uses it, because one column holds every attribute.
- Two-column rules name a column in the same table (`core_course`) or in another table filled from the same source row (`course.course_prefix`).

Whether a column can be blank, its type and its keys are never rules: they're already in the schema.

Every rule is `error` for now. The validator layer sets the ones the mock data breaks to `warning`, so squads still get data while the questions to Dena (/dev/color) are open. Every Milestone 1 English rule and what replaced it is in the team's planning notes (the Milestone 2.1 rule translation).

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
- **`UNKNOWN` means the file said it doesn't know; blank means it said nothing.** In text columns, the codes the dictionaries define as unknown or missing (`UK`, `Unknown`, `-1` where it means unknown, `M = Missing`, `MISSING`) are stored as `UNKNOWN`. Codes the dictionaries don't define, such as Pell's `U`, stay as written and fail their rule. In number, date and true/false columns both are blank.
- **Two `Race` labels are repaired.** The mock files cut them at 25 characters; they're stored in full (2,023 rows across both files).
- **The course file's `Cohort_Term` often disagrees with the cohort file.** The cohort file's value is kept. Why it disagrees is an open question for Dena (/dev/color).
- **The mock data has no institution column.** Everything belongs to one placeholder institution.

## Rebuild and test

Run from this folder:

```bash
./scripts/test_config.sh                                 # configs, schema, rule format and ER diagram
./scripts/test_rules.sh                                  # the rules module
```

Requires Python 3 and the packages in `requirements.txt` (`pip install -r requirements.txt`).

IDs in comments, such as D6, P9 or SC-1, refer to decisions and success criteria in the Milestone 1 and Milestone 2.1 specs, kept in the team's planning notes.
