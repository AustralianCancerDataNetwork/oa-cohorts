from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .measurable_resolver import get_measurable_registry
from .measurable_base import MeasurableBase, MeasurableDomain, MeasurableSpec, BoundMeasurableSpec

# Keep runtime imports light here. The concrete diagnosis measurables pull in
# OMOP-backed modifier modules with heavy import-time side effects, so we only
# import them when their exported names are actually requested.
# TODO: improve import-time side effects of OMOP-backed modifiers so we can import the 
# measurables in a more normal fashion.

if TYPE_CHECKING:
    from .dx_measurables import (
        AnyConditionMeasurable,
        PrimaryDiagnosisEpisodeMeasurable,
        StagedConditionMeasurable,
        MetsConditionMeasurable,
    )

_EXPORTS = {
    "AnyConditionMeasurable": (".dx_measurables", "AnyConditionMeasurable"),
    "PrimaryDiagnosisEpisodeMeasurable": (".dx_measurables", "PrimaryDiagnosisEpisodeMeasurable"),
    "StagedConditionMeasurable": (".dx_measurables", "StagedConditionMeasurable"),
    "MetsConditionMeasurable": (".dx_measurables", "MetsConditionMeasurable"),
}

__all__ = [
    "get_measurable_registry",
    "MeasurableBase",
    "MeasurableDomain",
    "MeasurableSpec",
    "BoundMeasurableSpec",
    "AnyConditionMeasurable",
    "PrimaryDiagnosisEpisodeMeasurable",
    "StagedConditionMeasurable",
    "MetsConditionMeasurable",
]


def __getattr__(name: str):
    """Resolve exported measurable classes lazily to avoid heavy import-time side effects."""
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
