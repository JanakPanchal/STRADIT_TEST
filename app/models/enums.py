import enum


class Role(str, enum.Enum):
    candidate = "candidate"
    admin = "admin"
    viewer = "viewer"   # staff, read-only: can monitor candidates and results, change nothing


class Stage(str, enum.Enum):
    aptitude = "aptitude"
    behaviour = "behaviour"
    coding = "coding"


STAGE_ORDER = [Stage.aptitude, Stage.behaviour, Stage.coding]


class QType(str, enum.Enum):
    mcq = "mcq"
    likert = "likert"
    coding = "coding"


class CandidateState(str, enum.Enum):
    REGISTERED = "REGISTERED"
    APTITUDE_IN_PROGRESS = "APTITUDE_IN_PROGRESS"
    APTITUDE_EVALUATED = "APTITUDE_EVALUATED"
    BEHAVIOUR_IN_PROGRESS = "BEHAVIOUR_IN_PROGRESS"
    BEHAVIOUR_EVALUATED = "BEHAVIOUR_EVALUATED"
    CODING_IN_PROGRESS = "CODING_IN_PROGRESS"
    CODING_EVALUATED = "CODING_EVALUATED"
    FINAL_RESULT = "FINAL_RESULT"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    DISQUALIFIED = "DISQUALIFIED"


class AttemptStatus(str, enum.Enum):
    active = "active"
    submitted = "submitted"
    expired = "expired"
    disqualified = "disqualified"


class ViolationType(str, enum.Enum):
    tab_switch = "tab_switch"
    fullscreen_exit = "fullscreen_exit"
    copy_paste = "copy_paste"
    resize = "resize"
    blur = "blur"
    devtools = "devtools"
    shortcut = "shortcut"


# Only these increment violation_count (resize/shortcut are informational).
COUNTED_VIOLATIONS = {
    ViolationType.tab_switch, ViolationType.fullscreen_exit, ViolationType.copy_paste,
    ViolationType.blur, ViolationType.devtools,
}
