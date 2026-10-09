# Measure Resolution

## `MeasureMember`: the atomic result row

When a measure executes, it materialises `MeasureMember` rows:

```python
@dataclass(frozen=True)
class MeasureMember:
    person_id: int
    measure_resolver: int
    episode_id: Optional[int]
    measure_date: Optional[date]
```

A `MeasureMember` is not just a boolean membership flag. It captures:

* who qualified
* under which resolver
* on what date

This preserved timing is what allows indicator windows, per-period reporting, and episode-aware logic to work later in the pipeline.

## Canonical Combination Semantics

Measures compose child results using the canonical member shape. In the shipped config this is primarily through `OR` and `AND`.

### `OR`: preserve all qualifying rows

`OR` logic unions child outputs without collapsing them.

Implementation shape:

* each child emits canonical rows
* rows are combined with `UNION ALL`
* all qualifying dates are preserved

Example:

* measure = ECOG 0 OR ECOG 1

If the same person qualifies on two different dates, both rows survive.

![Qualification temporal resolution](img/temporal_resolver_2.png)

### `AND`: resolver-aligned intersection

`AND` is not a simple person-level intersection. The same `measure_resolver` must satisfy all child criteria.

Implementation shape:

* child rows are aligned by `measure_resolver`
* the resulting qualification date is the latest child date
* rows with mismatched resolvers do not qualify together

This represents the earliest point at which all required conditions have become true for the same clinical context.

Example:

* measure = Stage III AND Radiotherapy

If Stage III qualifies under episode 10 and Radiotherapy qualifies under episode 10, the composite measure qualifies.

If the two child rows belong to different episodes, they do not intersect.

![Qualification temporal resolution](img/temporal_resolver_3.png)

### `FIRST` vs `ANY`

The engine compiles multiple SQL variants for measures and subqueries:

* `ANY`: preserve all qualifying rows
* `FIRST`: collapse to the earliest qualifying row per resolver
* `UNDATED`: keep qualifying membership without dates for intermediate set logic

### Temporal Window: Event-to-Event Timing

A third measure kind handles cases where qualification depends on the relationship between two events — specifically, whether event B occurs within a defined window relative to event A.

This is expressed via `MeasureTemporalWindow`, a separate config table with a 1:1 relationship to `Measure` (enforced by primary key). A measure with a `window_config` row is a temporal window measure.

**Anchor and candidate**

The anchor uses exactly one source: the measure's `subquery_id`, or `MeasureTemporalWindow.anchor_measure_id` pointing to another dated measure. A separate `candidate_measure_id` identifies the events evaluated relative to that anchor. A measure anchor runs its own filters first; only then is its earliest returned date selected per `(person_id, episode_id, measure_resolver)`. Existing subquery anchors keep their earliest-event behaviour. Missing references, two anchor sources, reversed bounds and dependency cycles are rejected before SQL compilation. 

**Window bounds**

`window_min_days` and `window_max_days` are optional. `NULL` means unbounded on that side. Negative values are valid — a candidate event occurring before the anchor can still qualify when it falls within the configured bounds. The emitted result remains the canonical `MeasureMember` row shape described above, so downstream comparison is based on the resolved `measure_date` selected by the window logic rather than on a separately emitted delta column.

**Pick strategy** (`window_pick_strategy`)

Controls which candidate event is retained per resolver after applying the window filter:

| Value | Behaviour |
|-------|-----------|
| `any` | All qualifying candidate events are preserved |
| `earliest` | One row per resolver: earliest qualifying candidate date |
| `latest` | One row per resolver: latest qualifying candidate date |
| `closest` | One row per resolver: candidate date closest to anchor date |

Default when `NULL`: `earliest`.

**Result date** (`result_date_source`)

Controls which date is emitted as `measure_date` in the output:

| Value | Emitted date |
|-------|-------------|
| `candidate` | Date of the candidate event |
| `anchor` | Date of the anchor event |
| `greatest` | Later of anchor or candidate |
| `least` | Earlier of anchor or candidate |

Default when `NULL`: `candidate`.

**Resolver alignment** (`require_same_resolver`)

When `TRUE` (default), the anchor and candidate are joined on both `person_id` and `measure_resolver`. When `FALSE`, the join is on `person_id` only.

**SQL variants**

`sql_any()` and `sql_first()` are supported. `sql_undated()` raises `NotImplementedError` — this surfaces as `WARN` (not `FAIL`) in `is_executable()` because the measure is still usable in the normal execution path.

**Configuration**

Temporal window measures are loaded via `measure_temporal_window.csv` in the config directory. The CSV is optional — existing deployments without it are unaffected.

Example — GP referral to treatment within 42 days:

```
Measure:
  name:        GP referral to first treatment or pall care <= 42d
  combination: rule_or
  subquery_id: <GP referral subquery>

MeasureTemporalWindow:
  candidate_measure_id: <any treatment OR pall care composite>
  window_min_days:      NULL
  window_max_days:      42
  window_pick_strategy: earliest
  result_date_source:   candidate
  require_same_resolver: TRUE
```

### Nested Measures

Measures can be nested arbitrarily:

```text
A AND (B OR C)
```

Evaluation proceeds bottom-up:

1. `B OR C` resolves to all qualifying child rows
2. `A` resolves independently
3. `AND` aligns rows on `measure_resolver`
4. the composite qualification date becomes the latest aligned child date

The canonical member shape is preserved at every level, which is why deeply nested logic can still feed report generation consistently.

![Qualification temporal resolution](img/temporal_resolver_1.png)
