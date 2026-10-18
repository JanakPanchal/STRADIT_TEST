from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import client_ip, get_own_attempt, require_candidate
from app.core.errors import ApiError, ok
from app.core.ratelimit import limiter
from app.core.security import utcnow
from app.db.session import get_db
from app.models import Attempt, Candidate, CodeDraft, CodeSubmission, Question, QType
from app.schemas.schemas import AnswerIn, CodeIn, CustomRunIn, DraftIn, ViolationsIn
from app.services import attempts as svc
from app.services.audit import audit
from app.services.judge0 import CaseResult, Judge0Client, JudgeUnavailable, get_judge

router = APIRouter(tags=["candidate"])
settings = get_settings()
MAX_RUNS_PER_MIN = 20
MAX_FINALS_PER_QUESTION = 20


@router.get("/tests")
def my_tests(cand: Candidate = Depends(require_candidate), db: Session = Depends(get_db)):
    return ok(svc.list_tests(db, cand), {"candidate_state": cand.state})


@router.post("/tests/{test_id}/start", status_code=201)
@limiter.limit(settings.submit_rate)
def start_test(request: Request, test_id: int, cand: Candidate = Depends(require_candidate),
               db: Session = Depends(get_db)):
    att, created = svc.start_attempt(db, cand, test_id, client_ip(request))
    if created:
        audit(db, cand.user_id, "test_start", "attempt", att.id, {"test_id": test_id}, ip=client_ip(request))
    db.commit()
    return ok({"attempt_id": att.id, "resumed": not created, "overview": svc.overview(db, att),
               "first_question": svc.get_question(db, att, 1)})


@router.get("/attempts/{attempt_id}/overview")
def attempt_overview(att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    out = svc.overview(db, att)
    db.commit()
    return ok(out)


@router.get("/attempts/{attempt_id}/question")
def get_question(n: int = Query(1, ge=1), att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    out = svc.get_question(db, att, n)
    db.commit()
    return ok(out)


@router.put("/attempts/{attempt_id}/answers/{qid}")
@limiter.limit("600/minute")
def save_answer(request: Request, qid: int, body: AnswerIn, att: Attempt = Depends(get_own_attempt),
                db: Session = Depends(get_db)):
    return ok(svc.save_answer(db, att, qid, body.selected))


@router.post("/attempts/{attempt_id}/violations")
@limiter.limit("120/minute")
def report_violations(request: Request, body: ViolationsIn, att: Attempt = Depends(get_own_attempt),
                      db: Session = Depends(get_db)):
    out = svc.record_violations(db, att, body.events)
    audit(db, att.candidate_test.candidate.user_id, "violations", "attempt", att.id,
          {"types": [e.type.value for e in body.events]}, ip=client_ip(request))
    db.commit()
    return ok(out)


@router.post("/attempts/{attempt_id}/submit")
@limiter.limit(settings.submit_rate)
def submit(request: Request, att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    svc.submit_attempt(db, att)
    audit(db, att.candidate_test.candidate.user_id, "test_submit", "attempt", att.id,
          {"reason": att.submit_reason}, ip=client_ip(request))
    db.commit()
    return ok(svc.result_view(db, att))


@router.get("/attempts/{attempt_id}/result")
def result(att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    svc.refresh_attempt(db, att)
    db.commit()
    return ok(svc.result_view(db, att))


# ------------------------------------------------------------------ coding
def _coding_question(db: Session, att: Attempt, qid: int) -> Question:
    svc.require_active(db, att)
    q = db.get(Question, qid)
    if not q or q.test_id != svc.attempt_test(db, att).id or q.type != QType.coding.value \
            or q.id not in svc.attempt_question_ids(db, att):
        raise ApiError(404, "not_found", "Coding question not found in this attempt")
    return q


def _judge(judge: Judge0Client, body: CodeIn, q: Question, cases) -> list[CaseResult]:
    try:
        return judge.judge(body.language, body.source_code, [(c.input, c.expected_output) for c in cases],
                           q.time_limit_ms, q.memory_limit_kb)
    except JudgeUnavailable:
        raise ApiError(503, "judge_unavailable", "Code runner is unavailable, please retry shortly")


def _save_sub(db: Session, att: Attempt, body: CodeIn, kind: str, res: list[CaseResult]) -> CodeSubmission:
    passed = sum(r.passed for r in res)
    worst = next((r.status for r in res if not r.passed), "Accepted")
    times = [r.time_ms for r in res if r.time_ms is not None]
    mems = [r.memory_kb for r in res if r.memory_kb is not None]
    sub = CodeSubmission(attempt_id=att.id, question_id=body.question_id, language=body.language,
                         source_code=body.source_code, kind=kind, status=worst, passed_cases=passed,
                         total_cases=len(res), runtime_ms=max(times) if times else None,
                         memory_kb=max(mems) if mems else None)
    db.add(sub)
    return sub


@router.post("/attempts/{attempt_id}/code/run")
@limiter.limit("120/minute")
def code_run(request: Request, body: CodeIn, att: Attempt = Depends(get_own_attempt),
             db: Session = Depends(get_db), judge: Judge0Client = Depends(get_judge)):
    """Runs against SAMPLE cases only."""
    q = _coding_question(db, att, body.question_id)
    _check_run_budget(db, att)
    samples = [c for c in q.test_cases if c.is_sample]
    res = _judge(judge, body, q, samples)
    _save_sub(db, att, body, "run", res)
    db.commit()
    return ok({"cases": [{"input": c.input, "expected_output": c.expected_output, "passed": r.passed,
                          "status": r.status, "stdout": r.stdout[:2000], "stderr": r.stderr[:2000],
                          "time_ms": r.time_ms, "memory_kb": r.memory_kb} for c, r in zip(samples, res)]})


def _check_run_budget(db: Session, att: Attempt) -> None:
    recent = db.query(CodeSubmission).filter(
        CodeSubmission.attempt_id == att.id, CodeSubmission.kind == "run",
        CodeSubmission.created_at > utcnow() - timedelta(minutes=1)).count()
    if recent >= MAX_RUNS_PER_MIN:
        raise ApiError(429, "run_limit", "Too many runs, wait a moment")


@router.post("/attempts/{attempt_id}/code/run-custom")
@limiter.limit("120/minute")
def code_run_custom(request: Request, body: CustomRunIn, att: Attempt = Depends(get_own_attempt),
                    db: Session = Depends(get_db), judge: Judge0Client = Depends(get_judge)):
    """Playground 'Run with my own input'. Counts against the same per-minute run budget."""
    q = _coding_question(db, att, body.question_id)
    _check_run_budget(db, att)
    try:
        res = judge.judge(body.language, body.source_code, [(body.stdin, None)], q.time_limit_ms, q.memory_limit_kb)[0]
    except JudgeUnavailable:
        raise ApiError(503, "judge_unavailable", "Code runner is unavailable, please retry shortly")
    db.add(CodeSubmission(attempt_id=att.id, question_id=q.id, language=body.language, source_code=body.source_code,
                          kind="run", status=res.status, passed_cases=0, total_cases=0,
                          runtime_ms=res.time_ms, memory_kb=res.memory_kb))
    db.commit()
    return ok({"status": res.status, "ok": res.passed, "stdout": res.stdout[:10000], "stderr": res.stderr[:5000],
               "time_ms": res.time_ms, "memory_kb": res.memory_kb})


@router.put("/attempts/{attempt_id}/code/draft")
@limiter.limit("240/minute")
def save_draft(request: Request, body: DraftIn, att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    """Editor autosave: one row per (attempt, question, language)."""
    q = _coding_question(db, att, body.question_id)
    row = db.query(CodeDraft).filter_by(attempt_id=att.id, question_id=q.id, language=body.language).first()
    if row:
        row.source_code, row.updated_at = body.source_code, utcnow()
    else:
        db.add(CodeDraft(attempt_id=att.id, question_id=q.id, language=body.language, source_code=body.source_code))
    try:
        db.commit()
    except Exception:  # concurrent first save for the same key: retry as an update
        db.rollback()
        db.query(CodeDraft).filter_by(attempt_id=att.id, question_id=q.id, language=body.language).update(
            {"source_code": body.source_code, "updated_at": utcnow()})
        db.commit()
    return ok({"saved_at": utcnow().isoformat() + "Z"})


@router.get("/attempts/{attempt_id}/code/submissions/{sid}")
def my_submission_source(sid: int, att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    """The candidate's OWN earlier code, so the playground can load a previous submission back into the editor."""
    s = db.query(CodeSubmission).filter_by(id=sid, attempt_id=att.id, kind="final").first()
    if not s:
        raise ApiError(404, "not_found", "Submission not found")
    return ok({"id": s.id, "language": s.language, "source_code": s.source_code, "status": s.status,
               "passed_cases": s.passed_cases, "total_cases": s.total_cases})


@router.get("/attempts/{attempt_id}/code/submissions")
def my_submissions(question_id: int, att: Attempt = Depends(get_own_attempt), db: Session = Depends(get_db)):
    """The candidate's own graded submissions for one problem (no hidden-case detail)."""
    _coding_question(db, att, question_id)
    rows = (db.query(CodeSubmission).filter_by(attempt_id=att.id, question_id=question_id, kind="final")
            .order_by(CodeSubmission.id.desc()).limit(30).all())
    return ok([{"id": r.id, "language": r.language, "status": r.status, "passed_cases": r.passed_cases,
                "total_cases": r.total_cases, "runtime_ms": r.runtime_ms, "memory_kb": r.memory_kb,
                "created_at": r.created_at.isoformat() + "Z"} for r in rows])


@router.post("/attempts/{attempt_id}/code/submit")
@limiter.limit("30/minute")
def code_submit(request: Request, body: CodeIn, att: Attempt = Depends(get_own_attempt),
                db: Session = Depends(get_db), judge: Judge0Client = Depends(get_judge)):
    """Judges against ALL cases. Hidden cases are never revealed — only the pass count."""
    q = _coding_question(db, att, body.question_id)
    finals = db.query(CodeSubmission).filter_by(attempt_id=att.id, question_id=q.id, kind="final").count()
    if finals >= MAX_FINALS_PER_QUESTION:
        raise ApiError(429, "submit_limit", "Submission limit reached for this question")
    res = _judge(judge, body, q, q.test_cases)
    sub = _save_sub(db, att, body, "final", res)
    db.commit()
    return ok({"status": sub.status, "passed_cases": sub.passed_cases, "total_cases": sub.total_cases,
               "runtime_ms": sub.runtime_ms, "memory_kb": sub.memory_kb})
