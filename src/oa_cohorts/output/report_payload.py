
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MeasureSummary(BaseModel):
    id: int
    measure_name: str
    measure_combination: str


class IndicatorPayload(BaseModel):

    indicator_id: int
    indicator_description: str
    indicator_reference: str | None = None

    display_label: str = ""
    display_reference: str | None = None
    display_benchmark: int | None = None
    display_benchmark_unit: str | None = None

    numerator_label: str
    denominator_label: str

    numerator_measure: MeasureSummary
    denominator_measure: MeasureSummary

    @model_validator(mode="before")
    @classmethod
    def _inherit_display_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("display_label") in (None, ""):
            data["display_label"] = data.get("indicator_description")
        if "display_reference" not in data:
            data["display_reference"] = data.get("indicator_reference")
        return data


class DashCohortDefinitionPayload(BaseModel):
    dash_cohort_def_id: int
    dash_cohort_def_name: str
    measure_id: int
    measure_count: int
    members: list[int] = Field(default_factory=list)


class DashCohortPayload(BaseModel):
    dash_cohort_id: int
    dash_cohort_name: str
    definitions: list[DashCohortDefinitionPayload]


class ReportMeasurePayload(BaseModel):
    measure_id: int
    materialised_measure_id: int | None = None
    refresh_date: datetime | None = None


class ReportPayload(BaseModel):
    report_name: str
    report_short_name: str
    report_description: str

    indicators: list[IndicatorPayload]
    report_cohorts: list[DashCohortPayload]
    report_measures: list[ReportMeasurePayload]


class CohortDemographyRow(BaseModel):
    person_id: int
    mrn: str | None = None
    year_of_birth: int | None = None
    death_datetime: datetime | None = None
    gender: str | None = None
    language_spoken: str | None = None
    country_of_birth: str | None = None
    post_code: int | None = None


class PivotCohortRow(BaseModel):
    episode_id: int | None = None
    measure_date: date | None = None
    measure_resolver: int
    person_id: int
    cohort_label: str
    subcohort_label: str | None = None
    measure_id: int


class PivotIndicatorRow(BaseModel):
    person_id: int
    measure_resolver: int

    numerator_date: date | None = None
    denominator_date: date | None = None

    numerator_measure_id: int
    denominator_measure_id: int
    indicator: int

    numerator_value: bool
    denominator_value: bool


class ReportBundle(BaseModel):
    report: ReportPayload
    cohort_demography: list[CohortDemographyRow]
    pivot_cohort: list[PivotCohortRow]
    pivot_indicators: list[PivotIndicatorRow]

    model_config = {"extra": "forbid"}
