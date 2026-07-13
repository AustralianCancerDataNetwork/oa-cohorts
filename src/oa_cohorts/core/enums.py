import enum

class RuleMatcher(str, enum.Enum):
    substring = 'substring' 
    exact = 'exact' 
    hierarchy = 'hierarchy' 
    absence = 'absence'
    presence = 'presence'
    hierarchyexclusion = 'hierarchyexclusion'
    scalar = 'scalar' 
    phenotype = 'phenotype' 
    predicate = 'predicate'
    
class RuleTemporality(str, enum.Enum):
    dt_any = "dt_any"

    # Diagnosis-related anchors
    dt_current_start = "dt_current_start"
    dt_stage = "dt_stage"
    dt_mets = "dt_mets"

    # Treatment anchors
    dt_treatment_start = "dt_treatment_start"
    dt_treatment_end = "dt_treatment_end"
    dt_concurrent = "dt_concurrent"
    dt_treat = "dt_treat"
    dt_rad = "dt_rad"
    dt_surg = "dt_surg"

    # Observation / measurement anchors
    dt_obs = "dt_obs"
    dt_proc_start = "dt_proc_start"
    dt_meas = "dt_meas"
    dt_consult = "dt_consult"

    # Person anchors
    dt_death = "dt_death"

    # Numerator / denominator semantics
    dt_numerator = "dt_numerator"
    dt_denominator = "dt_denominator"

    dt_visit = "dt_visit"

class RuleTarget(str, enum.Enum):
    # Diagnosis-related
    dx_any = "dx_any"
    dx_primary = "dx_primary"
    dx_first_primary = "dx_first_primary"
    dx_mets = "dx_mets"
    dx_stage = "dx_stage"

    # Treatment-related
    tx_any = "tx_any"
    tx_first_line = "tx_first_line"
    tx_current_episode = "tx_current_episode"
    tx_chemotherapy = "tx_chemotherapy"
    tx_radiotherapy = "tx_radiotherapy"
    tx_surgical = "tx_surgical"
    tx_concurrent = "tx_concurrent"

    # Demographics / person
    demog_gender = "demog_gender"
    demog_death = "demog_death"

    # Observation / procedure / measurement
    obs_value = "obs_value"
    obs_concept = "obs_concept"
    proc_concept = "proc_concept"
    meas_concept = "meas_concept"
    meas_value_num = "meas_value_num"
    meas_value_concept = "meas_value_concept"

    # Windows / derived scalars
    tx_to_death_window = "tx_to_death_window"
    dx_to_tx_window = "dx_to_tx_window"
    referral_to_tx_window = "referral_to_tx_window"
    referral_to_specialist_window = "referral_to_specialist_window"

    # Intent modifiers
    intent_rt = "intent_rt"
    intent_sact = "intent_sact"
    intent_concurrent_rt = "intent_concurrent_rt"

    ev_visit = "ev_visit"

class ThresholdDirection(str, enum.Enum):
    gt = ">"
    lt = "<"
    eq = "="
    neq = "!="

class RuleCombination(str, enum.Enum):
    rule_or = "or"
    rule_and = "and"
    rule_except = "except"

    @property
    def label(self) -> str:
        return self.value.upper()
    

class ReportStatus(str, enum.Enum):
    st_current = 'current'
    st_draft = 'draft'
    st_historical = 'historical'

class WindowPickStrategy(str, enum.Enum):
    any      = "any"
    earliest = "earliest"
    latest   = "latest"
    closest  = "closest"

class ResultDateSource(str, enum.Enum):
    anchor    = "anchor"
    candidate = "candidate"
    greatest  = "greatest"
    least     = "least"