from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.security import utcnow
from app.db.base import Base
from app.models.enums import AttemptStatus, CandidateState, QType, Role, Stage


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.candidate.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)   # staff accounts (candidates keep theirs on Candidate)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    candidate: Mapped["Candidate | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class College(Base):
    __tablename__ = "colleges"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)  # short id used in CSV uploads, e.g. IITB
    city: Mapped[str] = mapped_column(String(100), default="")
    state: Mapped[str] = mapped_column(String(100), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(String(200), default="")
    contact_name: Mapped[str] = mapped_column(String(200), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    contact_phone: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    candidates: Mapped[list["Candidate"]] = relationship(back_populates="college")


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    college_id: Mapped[int | None] = mapped_column(ForeignKey("colleges.id", ondelete="SET NULL"), nullable=True, index=True)
    branch: Mapped[str] = mapped_column(String(100), default="")
    batch: Mapped[str] = mapped_column(String(100), default="", index=True)
    state: Mapped[str] = mapped_column(String(40), default=CandidateState.REGISTERED.value, index=True)

    college: Mapped["College | None"] = relationship(back_populates="candidates")
    user: Mapped[User] = relationship(back_populates="candidate")
    candidate_tests: Mapped[list["CandidateTest"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class Test(Base):
    __tablename__ = "tests"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(20), default=Stage.aptitude.value, index=True)
    duration_min: Mapped[int] = mapped_column(Integer)
    start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cutoff_score: Mapped[float] = mapped_column(Float, default=0)
    max_violations: Mapped[int] = mapped_column(Integer, default=3)
    show_result: Mapped[bool] = mapped_column(Boolean, default=False)
    # question pool: each candidate is drawn this many questions at random (None = everyone gets all)
    questions_per_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    questions: Mapped[list["Question"]] = relationship(
        back_populates="test", cascade="all, delete-orphan", order_by="Question.id")


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20), default=QType.mcq.value)
    body: Mapped[str] = mapped_column(Text)
    # mcq/likert: [{"text": str, "score": float?}]
    options_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    correct_option: Mapped[int | None] = mapped_column(Integer, nullable=True)
    marks: Mapped[float] = mapped_column(Float, default=1)
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    # coding-only
    input_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    function_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=2000)
    memory_limit_kb: Mapped[int] = mapped_column(Integer, default=128000)

    test: Mapped[Test] = relationship(back_populates="questions")
    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="TestCase.id")


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False  # not a pytest class
    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    input: Mapped[str] = mapped_column(Text, default="")
    expected_output: Mapped[str] = mapped_column(Text, default="")
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped[Question] = relationship(back_populates="test_cases")


class CandidateTest(Base):
    __tablename__ = "candidate_tests"
    __table_args__ = (UniqueConstraint("candidate_id", "test_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))  # denormalised for the one-test-per-stage rule
    status: Mapped[str] = mapped_column(String(20), default="assigned")

    candidate: Mapped[Candidate] = relationship(back_populates="candidate_tests")
    test: Mapped[Test] = relationship()
    attempt: Mapped["Attempt | None"] = relationship(back_populates="candidate_test", uselist=False, cascade="all, delete-orphan")


class Attempt(Base):
    __tablename__ = "attempts"
    # one attempt per candidate per test (no silent retakes)
    __table_args__ = (UniqueConstraint("candidate_test_id"),
                      Index("ix_attempt_status_deadline", "status", "server_deadline"))
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_test_id: Mapped[int] = mapped_column(ForeignKey("candidate_tests.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    server_deadline: Mapped[datetime] = mapped_column(DateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=AttemptStatus.active.value)
    shuffle_seed: Mapped[int] = mapped_column(Integer)
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    start_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # snapshot of THIS candidate's questions, in the order they are served (None on pre-pool attempts)
    question_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    candidate_test: Mapped[CandidateTest] = relationship(back_populates="attempt")
    answers: Mapped[list["Answer"]] = relationship(back_populates="attempt", cascade="all, delete-orphan")
    violations: Mapped[list["Violation"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", order_by="Violation.id")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    selected_option: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ORIGINAL index
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score_awarded: Mapped[float | None] = mapped_column(Float, nullable=True)
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    attempt: Mapped[Attempt] = relationship(back_populates="answers")


class CodeSubmission(Base):
    __tablename__ = "code_submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String(20))
    source_code: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10), default="final")  # run | final
    status: Mapped[str] = mapped_column(String(40), default="pending")
    runtime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CodeDraft(Base):
    """Latest editor contents per (attempt, question, language): survives refresh / crash / another machine."""
    __tablename__ = "code_drafts"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", "language"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String(20))
    source_code: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Violation(Base):
    __tablename__ = "violations"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # SERVER time
    counted: Mapped[bool] = mapped_column(Boolean, default=True)
    client_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    attempt: Mapped[Attempt] = relationship(back_populates="violations")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PasswordToken(Base):
    """Single-use, expiring link token for 'forgot password' (reset) and invitations (invite). Only the hash is stored."""
    __tablename__ = "password_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))  # reset | invite
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
