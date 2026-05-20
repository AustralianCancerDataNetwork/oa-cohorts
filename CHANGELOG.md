## 0.1.1
- report runner

## 0.1.2 
- upversion constructs

## 0.1.3
- treatment measurables

## 0.1.4
- string matcher attributes

## 0.2.0
- centralised execution functions and upversion to beta status

## 0.2.1
- introduced special handling for measure 0

## 0.2.2
- added lenient execution mode

## 0.2.3
- added death measure
- fixed sql resolution for any/earliest

## 0.2.4
- report payload handler

## 0.2.5
- moved string field for conditions

## 0.2.6
- upversion constructs

## 0.2.7
- change scalar thresholding to be looking at correct fields

## 0.2.8
- report.report_measures property removed

## 0.3.0
- changed ownership of membership caching to the execution layer to avoid confusion in python objects pointing to same measure rows not sharing cached execution results

## 0.3.1
- report runner needs to skip measure id = 0

## 0.3.2
- apply strict parameter during pre-flight execution as well as full execution

## 0.3.3
- bump

## 0.3.4
- fixed measure name parameter in report runner

## 0.3.5
- passing corrected executor to report object

## 0.3.6
- changing return typing around demography

## 0.3.7
- upversion constructs

## 0.3.8 
- upversion constructs

## 0.3.9
- upversion constructs

## 0.3.10
- days tx to death / dx to tx

## 0.3.12
- treatment intent

## 0.3.13
- upversion constructs

## 0.3.14
- upversion constructs

## 0.3.15
- bump

## 0.3.16
- upversion constructs

## 0.3.17
- gotta actually save it

## 0.3.18
- predicate rule

## 0.3.19
- updated predicate rule

## 0.3.20
- updated predicate rule

## 0.3.21
- upversion constructs

## 0.3.22
- upversion constructs

## 0.4.0
- configuration interface
- fixed full-cohort resolution

## 0.4.1
- added functionality for dynamic date windowing

## 0.4.2
- up-version omop-alchemy & set minimum versions for other dependabot alerts

## 0.5.0
- removed report version table because we added cohort library and authorship service support, so it was no longer a value-add and just confused things

## 0.5.1 
- removed stale import logic

## 0.5.2
- concurrent treatment measurable

## 0.5.3 
- better typing in measurable base

## 0.5.4
- changes to handling of scalar queries to support ones that truly do not have a concept_id column that is mapped

## 0.6.0
- temporal window measure type: first-class event-to-event timing via `MeasureTemporalWindow`
- removed dead temporal columns from indicator table (`temporal_early`, `temporal_late`, `temporal_min`, `temporal_min_units`, `temporal_max`, `temporal_max_units`)
- `config_import` supports optional CSV tables
- removed notebooks directory until updated examples can be created

## 0.6.1
- updated handling of numerator and denominator labels