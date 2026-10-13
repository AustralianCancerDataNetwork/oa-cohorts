"""Comparison records clinical drift while rejecting broken export receipts."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "validation/episode-resolution/compare_reports.py"
SPEC = importlib.util.spec_from_file_location("resolution_compare", PATH)
COMPARISON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARISON)


def export_fixture(root, label, rows):
    payload = {"report": {"indicators": [{"indicator_id": 1, "indicator_description": "Invented assessment"}]},
               "pivot_cohort": [{"person_id": 1, "episode_id": 10, "measure_resolver": 10}],
               "pivot_indicators": rows}
    for directory in ("clinical", "metadata"):
        (root / directory).mkdir(exist_ok=True)
    path = root / "clinical" / f"report_1_{label}.json"
    path.write_text(json.dumps(payload))
    manifest = {"data_schema": COMPARISON.DATA, "config_schema": COMPARISON.CONFIG,
                "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "config_sha256": "same", "strict": True, "label": label, "report_id": 1,
                "versions": {"oa-cohorts": "0.11.1.dev3" if label == "baseline" else COMPARISON.CANDIDATE_VERSION,
                             "omop-constructs": "0.10.0", "omop-alchemy": "1.2.2"}}
    (root / "metadata" / path.name).write_text(json.dumps(manifest))


def row(passing=True, day="2026-01-01", eligible=True):
    return {"person_id": 1, "measure_resolver": 10, "indicator": 1,
            "numerator_value": passing, "denominator_value": eligible,
            "denominator_date": day, "numerator_date": day if passing else None}


def test_drift_is_counted_and_small_counts_are_masked(tmp_path):
    export_fixture(tmp_path, "baseline", [row(), row(day="2026-02-01")])
    export_fixture(tmp_path, "candidate", [row(False)])
    result = COMPARISON.compare_report(tmp_path, 1)
    assert result["cohort_identity_unchanged"]
    indicator = result["indicators"][0]
    assert indicator["baseline"]["extra_eligible_rows"] == "x"
    assert indicator["candidate"]["passing_units"] == 0
    assert indicator["drift"]["passes_lost"] == "x"
    assert indicator["drift"]["single_primary_outcomes_changed"] == "x"
    assert indicator["review_required"]


def test_content_and_configuration_drift_are_integrity_errors(tmp_path):
    export_fixture(tmp_path, "baseline", [row()])
    export_fixture(tmp_path, "candidate", [row()])
    path = tmp_path / "metadata/report_1_candidate.json"
    manifest = json.loads(path.read_text())
    manifest["config_sha256"] = "different"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="same configuration"):
        COMPARISON.compare_report(tmp_path, 1)
    (tmp_path / "clinical/report_1_candidate.json").write_text("{}")
    with pytest.raises(ValueError, match="differs from its manifest"):
        COMPARISON.compare_report(tmp_path, 1)


def test_configured_zero_row_indicator_is_included(tmp_path):
    export_fixture(tmp_path, "baseline", [])
    export_fixture(tmp_path, "candidate", [])
    indicator = COMPARISON.compare_report(tmp_path, 1)["indicators"][0]
    assert indicator["indicator_id"] == 1
    assert indicator["no_rows_in_either_export"]
    assert indicator["candidate"]["eligible_units"] == 0
    assert not indicator["review_required"]


def test_period_and_selected_evidence_drift_are_masked(tmp_path):
    before = {**row(), "period_date": "2026-01-01", "opportunity_id": 100,
              "passing_cancer_keys": [[1, 10, 10]]}
    after = {**before, "period_date": "2026-02-01", "opportunity_id": 200}
    export_fixture(tmp_path, "baseline", [before])
    export_fixture(tmp_path, "candidate", [after])
    indicator = COMPARISON.compare_report(tmp_path, 1)["indicators"][0]
    assert indicator["drift"]["period_date_changed"] == "x"
    assert indicator["drift"]["opportunity_id_changed"] == "x"
    assert indicator["drift"]["passing_cancer_keys_changed"] == 0
    assert indicator["detail_comparison_coverage"]["completion_date"] == "unavailable_in_one_or_both_exports"
    assert "completion_date_changed" not in indicator["drift"]
    assert indicator["review_required"]


def test_passing_units_removed_with_eligibility_are_separate(tmp_path):
    export_fixture(tmp_path, "baseline", [row()])
    export_fixture(tmp_path, "candidate", [row(False, eligible=False)])
    drift = COMPARISON.compare_report(tmp_path, 1)["indicators"][0]["drift"]
    assert drift["passes_lost"] == 0
    assert drift["passing_units_removed_with_eligibility"] == "x"


def test_older_candidate_requires_explicit_expected_version(tmp_path):
    export_fixture(tmp_path, "baseline", [row()])
    export_fixture(tmp_path, "candidate", [row()])
    path = tmp_path / "metadata/report_1_candidate.json"
    manifest = json.loads(path.read_text())
    manifest["versions"]["oa-cohorts"] = "0.12.0.dev2"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unexpected runtime"):
        COMPARISON.compare_report(tmp_path, 1)
    assert COMPARISON.compare_report(tmp_path, 1, "0.12.0.dev2")["cohort_identity_unchanged"]
