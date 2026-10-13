# Atomic Rule Types (`QueryRule`)

`QueryRule` is the smallest declarative unit in the engine.

Each rule:

* targets a field resolved by a `Subquery`
* produces a SQL `WHERE` clause fragment
* participates in rule-level `UNION ALL` composition inside the subquery

Choose a rule according to the field you want to test: a code, a number, a boolean flag or a string. The [measurable](measurables.md) supplies that field and determines which records are available to test. A rule filters those records; it does not decide which cancer an event belongs to or how many cancers or patients the indicator counts.

The examples below use invented concept IDs and records. Assume the example concepts and their stated relationships are present in the vocabulary; they are not clinical concept mappings to copy into a report. `NULL` means a missing value.

## Combining rules: either is different from both

During measure execution, multiple rules in one subquery are **alternatives**. Each rule selects records, and the results are combined with `UNION ALL`. For example, two exact rules for ECOG 0 and ECOG 1 mean “ECOG 0 **or** ECOG 1”, not “both”.

| Record | Recorded ECOG | Exact rule for ECOG 0 | Exact rule for ECOG 1 | Selected by the subquery? |
|---|---|---|---|---|
| M1 | 0 | Yes | No | Yes |
| M2 | 1 | No | Yes | Yes |
| M3 | 2 | No | No | No |

Here 0, 1 and 2 are the clinical score labels; the exact rules use the corresponding vocabulary concept IDs. If the source instead stores a numeric score, use a scalar rule against that numeric field.

To require stage III **and** radiotherapy, put those criteria in separate measures and combine them with `AND`. The criteria must match the same cancer. If the question concerns curative RT, modality and intent must also refer to the same treatment; combining arbitrary records from the same patient or cancer would not establish that. See [Combining criteria](measure_resolution.md#combining-criteria) and [treatment identity](evidence_and_indicators.md#a-question-about-first-treatment-must-use-the-first-treatment).

For developers calling SQL helpers directly: compiled measure execution uses `get_subquery_evidence()`, which combines rule alternatives. The lower-level `Subquery.select()` and `where_clause()` helpers combine rule predicates with `AND`; they are not an equivalent preview of a multi-rule measure.

---

## 1. `ExactRule`

### Semantics

Match rows whose concept-like field equals a single OMOP concept id.

### SQL shape

```sql
field = concept_id
```

### Typical use

Precise coded events such as a single diagnosis, procedure, or referral concept.

### Worked example

An exact rule with `concept_id=1100` keeps only records coded 1100. It does not include related concepts automatically.

| Recorded concept | Selected? | Why |
|---|---|---|
| 1100 | Yes | Exact match |
| 1000, the parent of 1100 | No | Related, but a different concept |
| 1200, another concept in the same family | No | Related, but a different concept |
| `NULL` | No | No value to match |

## 2. `HierarchyRule`

### Semantics

Expand one OMOP concept into its descendants using `concept_ancestor`, then match any descendant concept.

### SQL shape

```sql
field IN (descendant_concept_ids)
```

### Typical use

Broad diagnosis or procedure groupings such as stage families or disease branches.

### Worked example

Suppose the hierarchy rows for ancestor 1000 include 1000 itself and descendants 1100 and 1200. A hierarchy rule for 1000 accepts all three.

| Recorded concept | Selected? | Why |
|---|---|---|
| 1000 | Yes | The hierarchy contains the self-relationship 1000 → 1000 |
| 1100 | Yes | Descendant of 1000 |
| 1200 | Yes | Descendant of 1000 |
| 2000 | No | Outside that hierarchy |
| `NULL` | No | No value to match |

The rule reads `concept_ancestor`; it does not infer relationships from concept names or code prefixes. Parent matching depends on the self-relationship being present.

## 3. `HierarchyExclusionRule`

### Semantics

Exclude a concept and all of its descendants.

### SQL shape

```sql
field NOT IN (descendant_concept_ids)
```

### Typical use

Filtering individual records outside an unwanted concept family.

### Worked example

Using the same family `{1000, 1100, 1200}`, a hierarchy exclusion rule for 1000 selects records outside that set.

| Recorded concept | Selected? |
|---|---|
| 1000 | No |
| 1100 | No |
| 2000 | Yes |
| `NULL` | No: SQL `NOT IN` does not treat a null as a known value outside the set |

This filters **records**, not every record belonging to a cancer. If one cancer has records coded both 1100 and 2000, its 2000 record can still pass this rule. To exclude the cancer whenever it has evidence in the unwanted family, use a measure-level `EXCEPT` with that family as the exclusion criterion. A hierarchy exclusion alone also does not supply a positive definition such as “lung cancer”.

## 4. `PresenceRule`

### Semantics

Match rows where the resolved field is non-null.

### SQL shape

```sql
field IS NOT NULL
```

### Typical use

Checking whether the field supplied by the chosen measurable has a value.

### Worked example

For a concept-like field, presence means non-null; it does not mean “known, valid clinical code”.

| Field in an available row | `PresenceRule` selects it? |
|---|---|
| 1100 | Yes |
| 0 | Yes: zero is a value, not SQL `NULL` |
| `NULL` | No |
| No row supplied by the measurable | No row exists for the rule to select |

The target determines what is being checked. A non-null treatment field does not by itself establish that an assessment exists, that a date is known, or that an event falls inside an indicator window.

## 5. `AbsenceRule`

### Semantics

Match rows where the resolved field is null.

### SQL shape

```sql
field IS NULL
```

### Typical use

Finding missing values in rows supplied by a measurable.

### Worked example

| Field in an available row | `AbsenceRule` selects it? |
|---|---|
| 1100 | No |
| 0 | No: zero is not SQL `NULL` |
| `NULL` | Yes |
| No row supplied by the measurable | No row exists for the rule to select |

A measurable may deliberately provide one row per cancer with a null field when no linked record exists; in that case the rule can select that row. Without such a row, `AbsenceRule` cannot find the cancer by looking for nulls. Missing documentation also does not establish clinical absence. For “eligible cancers with no qualifying surgery evidence”, use an eligible-cancer measure `EXCEPT` a surgery measure, with the appropriate episode and timing definitions.

Existing definitions can also combine a dated positive criterion with an `AbsenceRule` through AND. For example, stage recorded on 10 January AND a supplied no-surgery row qualifies on 10 January. The absence condition has no event date and does not erase the stage date. A required event with a missing date still cannot establish a completion date. Absence alone cannot establish that something happened inside a date window.

## 6. `ScalarRule`

### Semantics

Apply a threshold comparison to a measurable's numeric value column.

Supported threshold directions:

* `>`
* `<`
* `=`
* `!=`

Scalar rules also carry a `threshold_comparator` target, which tells the engine which measurable exposes the numeric column to compare against.

### Concept filtering behavior

Scalar rules have two modes:

* `concept_id = 0`: threshold-only comparison, no concept restriction
* `concept_id != 0`: threshold comparison constrained to a concept-like field

This is an important current usage detail:

* numeric-only measurables are valid for threshold-only scalar rules
* concept-constrained scalar rules require the target measurable to expose both `value_numeric_attr` and `value_concept_attr`

### SQL shape

Threshold-only scalar rule:

```sql
numeric_value_column < threshold
```

Concept-constrained scalar rule:

```sql
field = concept_id
AND numeric_value_column < threshold
```

### Typical use

* treatment within 30 days of death
* diagnosis to treatment interval
* referral-to-specialist interval
* measurement thresholds such as ECOG, lab values, or scores

### Worked example: a time threshold

Suppose the measurable exposes an interval in whole days. With `concept_id=0`, `scalar_threshold=30` and `threshold_direction=lt`, the rule tests only whether that interval is less than 30. Set `threshold_comparator` to the measurable that supplies the interval.

| Interval supplied by the measurable | Selected by `< 30`? |
|---|---|
| −1 day | Yes: the rule has no lower bound |
| 29 days | Yes |
| 30 days | No: `<` is strict |
| 31 days | No |
| `NULL` | No |

“Less than 30” is different from “0 through 30 days, inclusive”. For a bounded event-to-event interval, a [temporal-window measure](measure_resolution.md#temporal-window-event-to-event-timing) makes both bounds and the compared dates explicit.

### Worked example: a numeric measurement

Suppose concept 3000 identifies a measurement whose numeric score must be less than 2. A concept-constrained scalar rule checks both the measurement concept and its value on the same row.

| Measurement concept | Numeric value | Selected by concept 3000 AND value `< 2`? |
|---|---:|---|
| 3000 | 1 | Yes |
| 3000 | 2 | No |
| 4000 | 1 | No: wrong measurement |
| 3000 | `NULL` | No |

With `concept_id=0`, the 4000 row would also pass the numeric threshold if the measurable supplies it. Use that mode when the measurable already identifies the required quantity, rather than to mix unrelated measurements.

## 7. `PredicateRule`

### Semantics

Match rows using a boolean predicate column exposed by the measurable.

Predicate rules use `concept_id` as a lightweight boolean flag rather than as an OMOP concept lookup:

* `concept_id is None`: predicate must be `TRUE`
* `concept_id = 1`: predicate must be `TRUE`
* `concept_id = 0`: predicate must be `FALSE`
* any other non-zero value is coerced to `TRUE`

### SQL shape

```sql
predicate_field IS TRUE
```

or

```sql
predicate_field IS FALSE
```

### Typical use

Derived boolean modifiers such as:

* treatment includes radiotherapy
* treatment includes systemic therapy
* concurrent chemo-radiotherapy flag

### Worked example

Suppose the measurable supplies a derived “includes RT” flag. With `concept_id=1` (or omitted), the rule selects true flags. With `concept_id=0`, it selects false flags.

| Flag supplied by the measurable | Select true (`concept_id=1`) | Select false (`concept_id=0`) |
|---|---|---|
| `TRUE` | Yes | No |
| `FALSE` | No | Yes |
| `NULL` | No | No |

An unknown flag is not a false flag. The predicate’s clinical meaning comes from the measurable that calculates it; the rule only tests that result.

## 8. `PhenotypeRule`

### Semantics

Expand a phenotype definition into a curated set of concepts and match any of them.

### SQL shape

```sql
field IN (phenotype_concept_ids)
```

### Typical use

Curated research or histology-driven definitions that do not map cleanly onto a single hierarchy.

### Worked example

Suppose phenotype 7 explicitly contains concepts 1100 and 2000. A `PhenotypeRule` with `phenotype_id=7` tests membership in that set.

| Recorded concept | Selected? |
|---|---|
| 1100 | Yes |
| 2000 | Yes |
| 1200 | No: not in the curated set |
| `NULL` | No |

Being related to a member is not enough. Include the desired descendants in the phenotype definition if the clinical question requires them.

## 9. `SubstringRule`

### Semantics

Match rows whose resolved string field contains the source concept's `concept_code`.

In practice this is usually used against measurable string fields that store code- or label-like values.

### SQL shape

```sql
field ILIKE '%concept_code%'
```

### Typical use

Matching code or label fragments where a suitable coded definition is unavailable.

### Worked example

Suppose the rule’s referenced concept has `concept_code='ABC'`. The rule uses that code as its search fragment against the measurable’s string field.

| String supplied by the measurable | Selected? | Why |
|---|---|---|
| `ABC.1` | Yes | Contains ABC |
| `abc.2` | Yes | Matching is case-insensitive |
| `XABCY` | Yes | The fragment can occur anywhere |
| `ABD.1` | No | Does not contain ABC |
| `NULL` | No | No string to match |

This is a contains test, not a prefix or vocabulary-hierarchy test. It does not validate the coding system. The `%` and `_` characters in a search fragment also act as SQL wildcards; they are not escaped as literal characters.

---

## Summary

| Rule type | Primary measurable field | Typical purpose |
|---|---|---|
| `ExactRule` | concept | precise coded match |
| `HierarchyRule` | concept | inclusive concept family |
| `HierarchyExclusionRule` | concept | exclusion family |
| `PresenceRule` | concept-like field | any recorded value |
| `AbsenceRule` | concept-like field | null value in an existing row |
| `ScalarRule` | numeric, optionally concept | thresholds and time windows |
| `PredicateRule` | predicate | derived booleans |
| `PhenotypeRule` | concept | curated concept-set expansion |
| `SubstringRule` | string | code/label substring match |

## From selected records to an indicator result

Rules keep the dates and cancer identities supplied by their measurable. They do not reassign evidence across primaries or reduce it to one patient result.

| Selected evidence for one patient | What it establishes |
|---|---|
| Two qualifying assessments for cancer A, on different dates | Both can remain available for later date-window checks |
| A qualifying assessment for cancer B | Evidence for B; it cannot satisfy A’s indicator through same-cancer matching |
| A record with a qualifying code but no date | Code matching can succeed; dated eligibility or a timing window still needs the required date |

[Episode attachment](episode_attachment.md) explains where evidence belongs. [Evidence and indicators](evidence_and_indicators.md) explains how dates, eligibility and counting cancers or patients determine the final result.
