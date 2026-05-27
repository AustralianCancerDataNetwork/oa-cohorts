# `Measurable` Abstraction

The `Measurable` layer is the bridge between the generic query engine and the underlying ORM or materialised-view schema.

It tells the engine:

* which ORM class to query for a given `RuleTarget`
* which columns identify the person, episode, and event date
* which optional columns support concept, numeric, string, or predicate-style filtering

The query engine never hard-codes source-table column names directly. It relies on the measurable contract instead.

## Canonical Output Contract

Every measurable must be able to emit rows in the canonical measure-member shape:

| Column | Meaning |
|---|---|
| `person_id` | individual identifier |
| `episode_id` | clinical episode |
| `measure_resolver` | alignment key used for higher-level joins |
| `measure_date` | date on which the measurable event qualifies |

`MeasurableBase.table_selectables()` and `filter_table_dated()` produce this shape for subqueries and measures.

## `MeasurableSpec`

`MeasurableSpec` is the declarative mapping attached to each measurable class.

Required attributes:

* `domain`
* `label`
* `person_id_attr`
* `episode_id_attr`
* `event_date_attr`

Optional value attributes:

* `value_concept_attr`
* `value_numeric_attr`
* `value_string_attr`
* `value_predicate_attr`

Optional control fields:

* `temporality_map`
* `valid_targets`

The spec stores attribute names, not SQLAlchemy columns. Those names are resolved against the concrete class at bind time.

## Binding

When a class subclasses `MeasurableBase` and defines:

```python
__measurable__ = MeasurableSpec(...)
```

the class is automatically bound to a `BoundMeasurableSpec` via `__init_subclass__`.

Binding rules:

* required columns are resolved immediately
* unsupported optional value channels remain `None`
* no fake SQL constants are injected for missing value channels

That last point is intentional. A measurable that does not support concept filtering should expose `value_concept_col = None`, and the query layer should decide whether that is acceptable for the rules being executed.

## Which Rule Types Need Which Columns

The current query engine resolves measurable fields like this:

| Rule style | Required measurable column |
|---|---|
| exact / hierarchy / hierarchy exclusion / presence / absence | `value_concept_attr` |
| substring | `value_string_attr` |
| predicate | `value_predicate_attr` |
| scalar threshold | `value_numeric_attr` |
| scalar threshold with `concept_id != 0` | `value_numeric_attr` and `value_concept_attr` |

### Scalar-only measurables

Derived window measurables can legitimately expose only a numeric column.

Examples in the current codebase:

* `ReferralToSpecialistWindowMeasurable`
* window-style derived thresholds generally driven by `ScalarRule`

These measurables work when the scalar rule is threshold-only, meaning `concept_id = 0`.

They do **not** support concept-constrained scalar filtering. If a scalar rule with `concept_id != 0` targets such a measurable, subquery compilation raises a targeted error explaining that `value_concept_attr` is required for concept filtering.

## Episode Requirement

This project assumes an episode-linked oncology data model.

Why that matters:

* a person can have multiple disease episodes
* diagnosis, treatment, and modifier rows must align to the correct episode
* composite `AND` logic joins on `measure_resolver`, which is usually the episode id

Without an episode-level resolver:

* `AND` logic would collapse to person-level intersection
* cross-episode contamination would become possible
* time-based qualification would be harder to interpret clinically

## Current Measurable Families

The shipped registry currently maps targets into a small set of measurable families:

* diagnosis measurables: any-diagnosis, primary-diagnosis, staging, metastasis
* event measurables: measurements, procedures, observations
* treatment measurables: surgery, chemotherapy, radiotherapy, intent, derived windows
* person measurables: death-linked person demography rows

## Current Target Registry

The authoritative supported set is the measurable registry, not the `RuleTarget` enum by itself.
Some enum members are reserved for future design work and do not currently resolve to a shipped measurable.

Current registry:

| Target | Measurable | Intended use |
|---|---|---|
| `dx_any` | `AnyConditionMeasurable` | any diagnosis row attached to an episode |
| `dx_primary` | `PrimaryDiagnosisEpisodeMeasurable` | diagnosis rows anchored to the primary diagnosis episode |
| `dx_stage` | `StagedConditionMeasurable` | staging modifiers linked to diagnosis episodes |
| `dx_mets` | `MetsConditionMeasurable` | metastatic disease modifiers linked to diagnosis episodes |
| `tx_surgical` | `SurgicalMeasurable` | cancer-related surgical procedures |
| `tx_current_episode` | `AllCurrentTreatmentMeasurable` | treatment-start summary for the current diagnosis episode |
| `tx_chemotherapy` | `ChemoTreatmentMeasurable` | chemotherapy treatment episodes |
| `tx_radiotherapy` | `RTTreatmentMeasurable` | radiotherapy treatment episodes |
| `intent_sact` | `IntentChemoMeasurable` | systemic therapy intent flags |
| `intent_rt` | `IntentRTMeasurable` | radiotherapy intent flags |
| `tx_to_death_window` | `TxDaysBeforeDeath` | derived days from treatment to death |
| `dx_to_tx_window` | `TxDaysToStartTreatment` | derived days from diagnosis to treatment start |
| `tx_concurrent` | `TxConcurrentChemoRT` | concurrent chemo-radiotherapy predicate |
| `referral_to_specialist_window` | `ReferralToSpecialistWindowMeasurable` | derived referral-to-specialist interval |
| `meas_concept` | `MeasurementMeasurable` | diagnosis-linked measurements |
| `proc_concept` | `ProcedureMeasurable` | diagnosis-linked procedures |
| `obs_concept` | `ObserveMeasurable` | diagnosis-linked observations |
| `ev_visit` | `VisitSpecialtyMeasurable` | diagnosis-linked specialty visits |
| `demog_death` | `DeathMeasurable` | death-linked person rows |

## Diagnosis Targets

For clinical users, the most important distinction is between the two diagnosis concept targets:

* `dx_any` asks whether a diagnosis row is present on the episode being evaluated
* `dx_primary` asks whether the diagnosis belongs to the patient's primary diagnosis episode anchor

`dx_primary` is intended for cohort definitions that should be stable at the start of the primary episode of care, rather than changing because later progression or metastatic episodes carry related diagnosis coding.

That design is deliberate. It lets the same reporting engine support both:

* broad episode-level diagnosis logic
* primary-diagnosis anchored cohort logic

without forcing report authors to invent bespoke SQL or special-case post-processing.

## String-backed Filters

`SubstringRule` works against the measurable's `value_string_attr`, but those string fields are not all the same kind of data.

Current examples include:

* diagnosis code strings such as `condition_code`
* derived labels such as `stage_label`
* event labels such as `event_label`
* treatment names such as `surgery_name`, `regimen_concept`, and `course_concept`
* provider specialty labels such as `provider_specialty`

That is why the measurable contract matters: the rule engine only knows that it is applying a substring filter to the target's declared string field.

See `src/oa_cohorts/measurables/measurable_resolver.py` for the authoritative target-to-class registry.
