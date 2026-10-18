from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.enums import QType, Stage, ViolationType

LANGUAGES = {"python", "java", "cpp", "c", "javascript"}


def _check_pw(v: str) -> str:
    if len(v.encode()) > 72:  # bcrypt hard limit
        raise ValueError("password must be at most 72 bytes")
    if len(v) < 8:
        raise ValueError("password must be at least 8 characters")
    return v


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str = Field(min_length=1, max_length=200)
    college_id: int | None = None
    branch: str = Field(default="", max_length=100)
    batch: str = Field(default="", max_length=100)
    _pw = field_validator("password")(_check_pw)


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str
    _pw = field_validator("new_password")(_check_pw)


class TestIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    stage: Stage
    duration_min: int = Field(gt=0, le=600)
    start_at: datetime | None = None
    end_at: datetime | None = None
    cutoff_score: float = Field(default=0, ge=0)
    max_violations: int = Field(default=3, ge=1, le=100)
    show_result: bool = False
    questions_per_attempt: int | None = Field(default=None, ge=1, le=500)
    is_active: bool = True

    @field_validator("start_at", "end_at")
    @classmethod
    def _to_naive_utc(cls, v):
        # browsers send tz-aware ISO strings; DB stores naive UTC
        if v is not None and v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    @model_validator(mode="after")
    def _window(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class OptionIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    score: float | None = None


class TestCaseIn(BaseModel):
    input: str = Field(default="", max_length=100_000)
    expected_output: str = Field(max_length=100_000)
    is_sample: bool = False


class QuestionIn(BaseModel):
    type: QType
    body: str = Field(min_length=1, max_length=20_000)
    options: list[OptionIn] | None = None
    correct_option: int | None = None
    marks: float = Field(default=1, gt=0, le=1000)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    input_format: str | None = None
    output_format: str | None = None
    function_signature: str | None = None
    time_limit_ms: int = Field(default=2000, ge=100, le=15000)
    memory_limit_kb: int = Field(default=128000, ge=16000, le=512000)
    test_cases: list[TestCaseIn] = []

    @model_validator(mode="after")
    def _by_type(self):
        if self.type == QType.mcq:
            if not self.options or not 2 <= len(self.options) <= 8:
                raise ValueError("mcq needs 2-8 options")
            if self.correct_option is None or not 0 <= self.correct_option < len(self.options):
                raise ValueError("correct_option must index an option")
        elif self.type == QType.likert:
            if not self.options or not 2 <= len(self.options) <= 10:
                raise ValueError("likert needs 2-10 options")
            if any(o.score is None for o in self.options):
                raise ValueError("every likert option needs a score")
        else:
            if not self.test_cases:
                raise ValueError("coding question needs test cases")
            if not any(t.is_sample for t in self.test_cases):
                raise ValueError("coding question needs at least one sample test case")
            if not any(not t.is_sample for t in self.test_cases):
                raise ValueError("coding question needs at least one hidden test case")
        return self


class CandidateIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    college_id: int | None = None
    branch: str = Field(default="", max_length=100)
    batch: str = Field(default="", max_length=100)
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return _check_pw(v) if v else v


class CandidateUpdate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    college_id: int | None = None
    branch: str = Field(default="", max_length=100)
    batch: str = Field(default="", max_length=100)
    is_active: bool = True


class CollegeIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=100)
    address: str = Field(default="", max_length=1000)
    website: str = Field(default="", max_length=200)
    contact_name: str = Field(default="", max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str = Field(default="", max_length=40, pattern=r"^[0-9+()\-\s]*$")
    notes: str = Field(default="", max_length=2000)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _upper(cls, v):
        return v.upper()


class BulkIds(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class AssignIn(BaseModel):
    test_id: int
    candidate_ids: list[int] = []
    batch: str | None = None


class AnswerIn(BaseModel):
    selected: int | None = Field(default=None, ge=0, le=20)  # position as shown to candidate


class ViolationEvent(BaseModel):
    type: ViolationType
    client_ts: str | None = Field(default=None, max_length=40)
    meta: dict[str, Any] | None = None

    @field_validator("meta")
    @classmethod
    def _small(cls, v):
        if v is not None and len(str(v)) > 500:
            raise ValueError("meta too large")
        return v


class ViolationsIn(BaseModel):
    events: list[ViolationEvent] = Field(min_length=1, max_length=50)


class CodeIn(BaseModel):
    question_id: int
    language: str
    source_code: str = Field(min_length=1, max_length=64_000)

    @field_validator("language")
    @classmethod
    def _lang(cls, v):
        if v not in LANGUAGES:
            raise ValueError(f"language must be one of {sorted(LANGUAGES)}")
        return v


class CustomRunIn(CodeIn):
    stdin: str = Field(default="", max_length=20_000)


class DraftIn(CodeIn):
    source_code: str = Field(max_length=64_000)  # empty allowed: the user may clear the editor


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str
    _pw = field_validator("new_password")(_check_pw)


class InviteIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=2000)
    all_pending: bool = False   # everyone who has never signed in


class StaffIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: str = Field(pattern="^(admin|viewer)$")


class StaffUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    role: str = Field(pattern="^(admin|viewer)$")
    is_active: bool = True
