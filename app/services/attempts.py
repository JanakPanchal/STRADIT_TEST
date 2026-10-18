"""Server-authoritative assessment engine: gating, timing, shuffling, scoring, violations."""
import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import utcnow
from app.models import (COUNTED_VIOLATIONS, CodeDraft, STAGE_ORDER, Answer, Attempt, AttemptStatus, Candidate,
                        CandidateState, CandidateTest, CodeSubmission, Question, QType, Stage, Test,
                        Violation, ViolationType)

settings = get_settings()
STAGE_NAMES = [s.value for s in STAGE_ORDER]
IN_PROGRESS_STATE = {"aptitude": CandidateState.APTITUDE_IN_PROGRESS,
                     "behaviour": CandidateState.BEHAVIOUR_IN_PROGRESS,
                     "coding": CandidateState.CODING_IN_PROGRESS}
PASSED_STATE = {"aptitude": CandidateState.APTITUDE_EVALUATED,
                "behaviour": CandidateState.BEHAVIOUR_EVALUATED,
                "coding": CandidateState.FINAL_RESULT}
BLOCKED_STATES = {CandidateState.CANDIDATE_REJECTED.value, CandidateState.DISQUALIFIED.value}


# ---------------------------------------------------------------- shuffling
def _h(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def question_order(seed: int, ids: list[int]) -> list[int]:
    """Deterministic per-attempt order (hash-sort: stable across Python versions)."""
    return sorted(ids, key=lambda i: _h(seed, f"q{i}"))


def option_perm(seed: int, qid: int, n: int) -> list[int]:
    """perm[display_position] = original option index."""
    return sorted(range(n), key=lambda i: _h(seed, f"o{qid}:{i}"))


# ---------------------------------------------------------------- gating
def attempt_view_status(ct: CandidateTest) -> str:
    a = ct.attempt
    if not a:
        return "assigned"
    if a.status == AttemptStatus.active.value:
        return "in_progress"
    if a.status == AttemptStatus.disqualified.value:
        return "disqualified"
    return "passed" if a.passed else "failed"


def availability(db: Session, cand: Candidate, ct: CandidateTest, all_cts: list[CandidateTest]) -> tuple[str, str | None]:
    """('available'|'locked'|'not_open'|'closed'|<attempt status>, reason)."""
    s = attempt_view_status(ct)
    if s != "assigned":
        return s, None
    if cand.state in BLOCKED_STATES:
        return "locked", "You are not eligible to continue"
    idx = STAGE_NAMES.index(ct.stage)
    for other in all_cts:
        if STAGE_NAMES.index(other.stage) < idx and attempt_view_status(other) != "passed":
            return "locked", f"Clear the {other.stage} stage first"
    t, now = ct.test, utcnow()
    if t.start_at and now < t.start_at:
        return "not_open", f"Opens at {t.start_at.isoformat()}Z"
    if t.end_at and now >= t.end_at:
        return "closed", "Test window has ended"
    return "available", None


def _cts(db: Session, cand: Candidate) -> list[CandidateTest]:
    return (db.query(CandidateTest).options(selectinload(CandidateTest.attempt), selectinload(CandidateTest.test))
            .filter(CandidateTest.candidate_id == cand.id).all())


def list_tests(db: Session, cand: Candidate) -> list[dict]:
    cts = sorted([c for c in _cts(db, cand) if c.test.is_active], key=lambda c: STAGE_NAMES.index(c.stage))
    out = []
    qcounts = dict(db.query(Question.test_id, func.count(Question.id))
                   .filter(Question.test_id.in_([c.test_id for c in cts] or [0]))
                   .group_by(Question.test_id).all())
    for ct in cts:
        if ct.attempt:
            refresh_attempt(db, ct.attempt)  # lazy expiry so status is never stale
        st, reason = availability(db, cand, ct, cts)
        out.append({"test_id": ct.test_id, "title": ct.test.title, "stage": ct.stage,
                    "duration_min": ct.test.duration_min, "start_at": ct.test.start_at,
                    "end_at": ct.test.end_at, "max_violations": ct.test.max_violations,
                    "cutoff_score": ct.test.cutoff_score, "question_count": qcounts.get(ct.test_id, 0), "status": st, "reason": reason,
                    "attempt_id": ct.attempt.id if ct.attempt else None})
    db.commit()
    return out


# ---------------------------------------------------------------- lifecycle
def remaining_seconds(att: Attempt) -> int:
    return max(0, int((att.server_deadline - utcnow()).total_seconds()))


def refresh_attempt(db: Session, att: Attempt) -> Attempt:
    """Lazy server-side expiry: any touch after the deadline closes the attempt."""
    if att.status == AttemptStatus.active.value and \
            utcnow() > att.server_deadline + timedelta(seconds=settings.answer_grace_seconds):
        finalize(db, att, "timeout", AttemptStatus.expired)
    return att


def require_active(db: Session, att: Attempt) -> Attempt:
    refresh_attempt(db, att)
    if att.status != AttemptStatus.active.value:
        db.commit()  # persist a lazy expiry that just happened; the error below would otherwise roll it back
        raise ApiError(409, "attempt_closed", "This attempt is already closed")
    return att


def start_attempt(db: Session, cand: Candidate, test_id: int, ip: str | None) -> tuple[Attempt, bool]:
    cts = _cts(db, cand)
    ct = next((c for c in cts if c.test_id == test_id), None)
    if not ct or not ct.test.is_active:
        raise ApiError(404, "not_found", "Test not found")
    if ct.attempt:
        refresh_attempt(db, ct.attempt)
        if ct.attempt.status == AttemptStatus.active.value:
            db.commit()
            return ct.attempt, False  # resume
        raise ApiError(409, "already_attempted", "You have already attempted this test")
    st, reason = availability(db, cand, ct, cts)
    if st != "available":
        raise ApiError(403, f"test_{st}", reason or "Test is not available")
    qcount = db.query(func.count(Question.id)).filter(Question.test_id == test_id).scalar()
    if not qcount:
        raise ApiError(409, "empty_test", "This test has no questions yet")
    now = utcnow()
    deadline = now + timedelta(minutes=ct.test.duration_min)
    if ct.test.end_at:
        deadline = min(deadline, ct.test.end_at)  # late starters can't outlast the window
    seed = secrets.randbits(31)
    pool = [qid for (qid,) in db.query(Question.id).filter(Question.test_id == test_id)]
    served = question_order(seed, pool)  # per-candidate random order...
    if ct.test.questions_per_attempt:
        served = served[:min(ct.test.questions_per_attempt, len(served))]  # ...and a per-candidate random subset
    att = Attempt(candidate_test_id=ct.id, stage=ct.stage, started_at=now, server_deadline=deadline,
                  shuffle_seed=seed, start_ip=ip, question_ids=served)
    db.add(att)
    cand.state = IN_PROGRESS_STATE[ct.stage].value
    ct.status = "started"
    try:
        db.flush()
    except IntegrityError:  # double-click race: UNIQUE(candidate_test_id) won for the other request
        db.rollback()
        existing = db.query(Attempt).filter(Attempt.candidate_test_id == ct.id).one()
        return existing, False
    return att, True


def _test_questions(db: Session, test_id: int) -> list[Question]:
    return (db.query(Question).options(selectinload(Question.test_cases))
            .filter(Question.test_id == test_id).order_by(Question.id).all())


def attempt_test(db: Session, att: Attempt) -> Test:
    return db.get(Test, db.get(CandidateTest, att.candidate_test_id).test_id)


def attempt_question_ids(db: Session, att: Attempt) -> list[int]:
    if att.question_ids is not None:
        return list(att.question_ids)
    ids = [qid for (qid,) in db.query(Question.id).filter(Question.test_id == attempt_test(db, att).id)]
    return question_order(att.shuffle_seed, ids)  # attempts created before the pool feature


def ordered_questions(db: Session, att: Attempt) -> list[Question]:
    by_id = {q.id: q for q in _test_questions(db, attempt_test(db, att).id)}
    return [by_id[i] for i in attempt_question_ids(db, att) if i in by_id]


def present_question(att: Attempt, q: Question, n: int, saved: Answer | None) -> dict:
    """Candidate-safe view: never includes correct_option, option scores or hidden cases."""
    d = {"n": n, "question_id": q.id, "type": q.type, "body": q.body, "marks": q.marks}
    if q.type in (QType.mcq.value, QType.likert.value):
        perm = option_perm(att.shuffle_seed, q.id, len(q.options_json))
        d["options"] = [q.options_json[i]["text"] for i in perm]
        sel = None
        if saved and saved.selected_option is not None:
            sel = perm.index(saved.selected_option)
        d["selected"] = sel
    else:
        d.update(input_format=q.input_format, output_format=q.output_format,
                 function_signature=q.function_signature, time_limit_ms=q.time_limit_ms,
                 memory_limit_kb=q.memory_limit_kb,
                 samples=[{"input": t.input, "expected_output": t.expected_output}
                          for t in q.test_cases if t.is_sample])
    return d


def get_question(db: Session, att: Attempt, n: int) -> dict:
    require_active(db, att)
    qs = ordered_questions(db, att)
    if not 1 <= n <= len(qs):
        raise ApiError(404, "not_found", "No such question")
    q = qs[n - 1]
    saved = db.query(Answer).filter_by(attempt_id=att.id, question_id=q.id).first()
    out = present_question(att, q, n, saved)
    if q.type == QType.coding.value:
        rows = db.query(CodeDraft).filter_by(attempt_id=att.id, question_id=q.id).order_by(CodeDraft.updated_at).all()
        out["drafts"] = {d.language: d.source_code for d in rows}
        out["draft_times"] = {d.language: d.updated_at.isoformat() + "Z" for d in rows}
        out["last_language"] = rows[-1].language if rows else None
    out.update(total=len(qs), remaining_seconds=remaining_seconds(att))
    return out


def overview(db: Session, att: Attempt) -> dict:
    require_active(db, att)
    qs = ordered_questions(db, att)
    answered = {a.question_id for a in db.query(Answer).filter_by(attempt_id=att.id)
                if a.selected_option is not None}
    submitted_code = {c.question_id for c in db.query(CodeSubmission).filter_by(attempt_id=att.id, kind="final")}
    return {"attempt_id": att.id, "stage": att.stage, "total": len(qs),
            "remaining_seconds": remaining_seconds(att),
            "violation_count": att.violation_count,
            "max_violations": attempt_test(db, att).max_violations,
            "questions": [{"n": i + 1, "type": q.type,
                           "answered": q.id in answered or q.id in submitted_code}
                          for i, q in enumerate(qs)]}


def save_answer(db: Session, att: Attempt, qid: int, selected: int | None) -> dict:
    require_active(db, att)
    q = db.get(Question, qid)
    test_id = attempt_test(db, att).id
    if not q or q.test_id != test_id or qid not in attempt_question_ids(db, att):
        raise ApiError(404, "not_found", "Question not in this attempt")
    if q.type == QType.coding.value:
        raise ApiError(400, "wrong_question_type", "Use the code endpoints for coding questions")
    row = db.query(Answer).filter_by(attempt_id=att.id, question_id=qid).first()
    if selected is None:
        if row:
            db.delete(row)
        db.commit()
        return {"saved": True, "selected": None}
    if selected >= len(q.options_json):
        raise ApiError(422, "validation_error", "Option out of range")
    original = option_perm(att.shuffle_seed, qid, len(q.options_json))[selected]
    if row:
        row.selected_option = original
    else:
        db.add(Answer(attempt_id=att.id, question_id=qid, selected_option=original))
    try:
        db.commit()
    except IntegrityError:  # concurrent autosave inserted first -> update instead (idempotent upsert)
        db.rollback()
        db.query(Answer).filter_by(attempt_id=att.id, question_id=qid).update({"selected_option": original})
        db.commit()
    return {"saved": True, "selected": selected}


# ---------------------------------------------------------------- scoring
def compute_score(db: Session, att: Attempt) -> tuple[float, float]:
    mine = set(attempt_question_ids(db, att))  # only the questions this candidate was actually served
    qs = [q for q in _test_questions(db, attempt_test(db, att).id) if q.id in mine]
    answers = {a.question_id: a for a in db.query(Answer).filter_by(attempt_id=att.id)}
    subs: dict[int, float] = {}
    for s in db.query(CodeSubmission).filter_by(attempt_id=att.id, kind="final"):
        if s.total_cases:
            subs[s.question_id] = max(subs.get(s.question_id, 0.0), s.passed_cases / s.total_cases)
    score = max_score = 0.0
    for q in qs:
        if q.type == QType.mcq.value:
            max_score += q.marks
            a = answers.get(q.id)
            if a and a.selected_option is not None:
                a.is_correct = a.selected_option == q.correct_option
                a.score_awarded = q.marks if a.is_correct else 0.0
                score += a.score_awarded
        elif q.type == QType.likert.value:
            max_score += max(o["score"] for o in q.options_json)
            a = answers.get(q.id)
            if a and a.selected_option is not None:
                a.score_awarded = float(q.options_json[a.selected_option]["score"])
                score += a.score_awarded
        else:
            max_score += q.marks
            score += q.marks * subs.get(q.id, 0.0)
    return round(score, 4), round(max_score, 4)


def finalize(db: Session, att: Attempt, reason: str, status: AttemptStatus = AttemptStatus.submitted) -> Attempt:
    """Idempotent: the conditional UPDATE lets exactly one caller (request or sweeper) win."""
    db.flush()  # autoflush is off: persist pending changes (e.g. violation_count) before refresh()
    won = (db.query(Attempt).filter(Attempt.id == att.id, Attempt.status == AttemptStatus.active.value)
           .update({"status": status.value, "submitted_at": utcnow(), "submit_reason": reason},
                   synchronize_session=False))
    db.refresh(att)
    if not won:
        return att
    test = attempt_test(db, att)
    att.score, att.max_score = compute_score(db, att)
    att.passed = status != AttemptStatus.disqualified and att.score >= test.cutoff_score
    ct = db.get(CandidateTest, att.candidate_test_id)
    ct.status = "completed"
    cand = db.get(Candidate, ct.candidate_id)
    if status == AttemptStatus.disqualified:
        cand.state = CandidateState.DISQUALIFIED.value
    elif att.passed:
        # cleared every stage that is assigned to them => final result (don't wait for stages never assigned)
        later = [x for x in STAGE_NAMES[STAGE_NAMES.index(att.stage) + 1:]]
        has_later = later and db.query(CandidateTest).filter(
            CandidateTest.candidate_id == cand.id, CandidateTest.stage.in_(later)).count()
        cand.state = (PASSED_STATE[att.stage] if has_later else CandidateState.FINAL_RESULT).value
    else:
        cand.state = CandidateState.CANDIDATE_REJECTED.value
    db.flush()
    return att


def submit_attempt(db: Session, att: Attempt) -> Attempt:
    refresh_attempt(db, att)
    if att.status == AttemptStatus.active.value:
        finalize(db, att, "manual")
    db.commit()  # second submit just returns the first result (state-idempotent)
    return att


def result_view(db: Session, att: Attempt) -> dict:
    test = attempt_test(db, att)
    d = {"attempt_id": att.id, "stage": att.stage, "status": att.status, "submit_reason": att.submit_reason}
    if att.status == AttemptStatus.active.value:
        raise ApiError(409, "attempt_active", "Attempt not finished yet")
    if test.show_result:
        d.update(score=att.score, max_score=att.max_score, cutoff=test.cutoff_score, passed=att.passed)
    else:
        d["message"] = "Your responses have been recorded. Results will be shared by the admin."
    return d


# ---------------------------------------------------------------- proctoring
def record_violations(db: Session, att: Attempt, events: list) -> dict:
    require_active(db, att)
    test = attempt_test(db, att)
    last = (db.query(func.max(Violation.occurred_at))
            .filter(Violation.attempt_id == att.id, Violation.counted.is_(True)).scalar())
    for ev in events:
        now = utcnow()
        counted = ev.type in COUNTED_VIOLATIONS
        # blur + visibilitychange + fullscreenchange fire together for one real action: collapse them
        if counted and last and (now - last).total_seconds() < settings.violation_cluster_seconds:
            counted = False
        elif counted:
            last = now
        meta = dict(ev.meta or {})
        if ev.client_ts:
            meta["client_ts"] = ev.client_ts
        db.add(Violation(attempt_id=att.id, type=ev.type.value, occurred_at=now, counted=counted,
                         client_meta=meta or None))
        if counted:
            att.violation_count += 1
    auto = False
    if att.violation_count >= test.max_violations:
        finalize(db, att, "violation_limit")
        auto = True
    db.commit()
    return {"violation_count": att.violation_count, "max_violations": test.max_violations,
            "auto_submitted": auto}


def disqualify(db: Session, cand: Candidate) -> None:
    """Manual admin disqualification: closes any active attempt, blocks further stages."""
    for ct in _cts(db, cand):
        if ct.attempt and ct.attempt.status == AttemptStatus.active.value:
            finalize(db, ct.attempt, "admin_disqualified", AttemptStatus.disqualified)
    cand.state = CandidateState.DISQUALIFIED.value


def sweep_expired(db: Session) -> int:
    """Background sweeper: finalize attempts whose deadline passed even if the browser is gone."""
    cutoff = utcnow() - timedelta(seconds=settings.answer_grace_seconds)
    rows = (db.query(Attempt).filter(Attempt.status == AttemptStatus.active.value,
                                     Attempt.server_deadline < cutoff).limit(200).all())
    for a in rows:
        finalize(db, a, "timeout", AttemptStatus.expired)
    db.commit()
    return len(rows)


def recompute_state(db: Session, cand: Candidate) -> None:
    """Re-derive candidate.state from what remains (after a retake reset, an unassign or a deleted test).
    A manual DISQUALIFIED is sticky."""
    if cand.state == CandidateState.DISQUALIFIED.value:
        return
    cts = sorted(db.query(CandidateTest).filter_by(candidate_id=cand.id).all(), key=lambda c: STAGE_NAMES.index(c.stage))
    attempts = [(c, db.query(Attempt).filter_by(candidate_test_id=c.id).first()) for c in cts]
    done = [(c, a) for c, a in attempts if a]
    if any(a.status == AttemptStatus.active.value for _, a in done):
        c, _ = next((c, a) for c, a in done if a.status == AttemptStatus.active.value)
        cand.state = IN_PROGRESS_STATE[c.stage].value
    elif any(a.status == AttemptStatus.disqualified.value for _, a in done):
        cand.state = CandidateState.DISQUALIFIED.value
    elif any(not a.passed for _, a in done):
        cand.state = CandidateState.CANDIDATE_REJECTED.value
    elif done:
        last_c = done[-1][0]
        later = [c for c in cts if STAGE_NAMES.index(c.stage) > STAGE_NAMES.index(last_c.stage)]
        cand.state = (PASSED_STATE[last_c.stage] if later else CandidateState.FINAL_RESULT).value
    else:
        cand.state = CandidateState.REGISTERED.value
