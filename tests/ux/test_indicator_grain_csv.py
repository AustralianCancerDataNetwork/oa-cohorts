import csv

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from oa_cohorts.cli.config_import import import_config_directory
from oa_cohorts.query.indicator import Indicator
from tests.ux.test_config_import import _build_config_dir


def test_old_csv_defaults_and_preserves_patient_grain_on_update(tmp_path):
    config = _build_config_dir(tmp_path / "config")
    engine = sa.create_engine("sqlite://")
    with Session(engine) as session:
        import_config_directory(config, session)
        indicator = session.get(Indicator, 1)
        assert indicator.grain == "episode"
        indicator.grain = "patient"
        session.commit()
        import_config_directory(config, session)
        assert session.get(Indicator, 1).grain == "patient"


@pytest.mark.parametrize("grain", ["episode", "patient", "invalid", ""])
def test_explicit_csv_grain_roundtrip_or_rejection(tmp_path, grain):
    config = _build_config_dir(tmp_path / "config")
    path = config / "indicator.csv"
    with path.open() as source:
        reader = csv.DictReader(source)
        fields = [*reader.fieldnames, "grain"]
        rows = [dict(row, grain=grain) for row in reader]
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with Session(sa.create_engine("sqlite://")) as session:
        if grain in ("episode", "patient"):
            import_config_directory(config, session)
            assert session.get(Indicator, 1).grain == grain
        else:
            with pytest.raises(ValueError):
                import_config_directory(config, session)
