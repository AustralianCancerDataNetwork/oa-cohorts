
from ..core import RuleTarget
from .measurable_base import MeasurableBase


def get_measurable_registry() -> dict[RuleTarget, type[MeasurableBase]]:
    from .dx_measurables import (
        AnyConditionMeasurable,
        MetsConditionMeasurable,
        PrimaryDiagnosisEpisodeMeasurable,
        StagedConditionMeasurable,
    )
    from .ev_measureables import (
        MeasurementMeasurable,
        MeasurementValueConceptMeasurable,
        ObserveMeasurable,
        ProcedureMeasurable,
        VisitSpecialtyMeasurable,
    )
    from .pr_measurables import DeathMeasurable
    from .tx_measurables import (
        AllCurrentTreatmentMeasurable,
        ChemoTreatmentMeasurable,
        IntentChemoMeasurable,
        IntentConcurrentRTMeasurable,
        IntentRTMeasurable,
        ReferralToSpecialistWindowMeasurable,
        RTTreatmentMeasurable,
        SurgicalMeasurable,
        TxConcurrentChemoRT,
        TxDaysBeforeDeath,
        TxDaysToStartTreatment,
    )

    return {
        RuleTarget.dx_any: AnyConditionMeasurable,
        RuleTarget.dx_primary: PrimaryDiagnosisEpisodeMeasurable,
        RuleTarget.dx_stage: StagedConditionMeasurable,
        RuleTarget.dx_mets: MetsConditionMeasurable,
        RuleTarget.tx_surgical: SurgicalMeasurable,
        RuleTarget.tx_current_episode: AllCurrentTreatmentMeasurable,
        RuleTarget.meas_concept: MeasurementMeasurable,
        RuleTarget.proc_concept: ProcedureMeasurable,
        RuleTarget.obs_concept: ObserveMeasurable,
        RuleTarget.tx_chemotherapy: ChemoTreatmentMeasurable,
        RuleTarget.tx_radiotherapy: RTTreatmentMeasurable,
        RuleTarget.intent_sact: IntentChemoMeasurable,
        RuleTarget.intent_rt: IntentRTMeasurable,
        RuleTarget.intent_concurrent_rt: IntentConcurrentRTMeasurable,
        RuleTarget.demog_death: DeathMeasurable,
        RuleTarget.tx_to_death_window: TxDaysBeforeDeath,
        RuleTarget.dx_to_tx_window: TxDaysToStartTreatment,
        RuleTarget.tx_concurrent: TxConcurrentChemoRT,
        RuleTarget.referral_to_specialist_window: ReferralToSpecialistWindowMeasurable,
        RuleTarget.ev_visit: VisitSpecialtyMeasurable,
        RuleTarget.meas_value_concept: MeasurementValueConceptMeasurable,
    }
