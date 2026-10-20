from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.api.v1.admin_tests import page_params
from app.api.v1.auth import issue_token
from app.core.config import get_settings
from app.services import mail
from app.core.deps import client_ip, require_admin
from app.core.errors import ApiError, ok
from app.core.security import generate_password, hash_password, utcnow
from app.db.session import get_db
from app.models import (RefreshToken, College, Answer, Attempt, AuditLog, Candidate, CandidateTest, CodeSubmission,
                        Question, Role, STAGE_ORDER, Test, User, Violation)
from app.schemas.schemas import AssignIn, BulkIds, CandidateIn, CandidateUpdate, InviteIn
from app.services import attempts as svc
from app.services import csvio, insights, personality
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin-candidates"])


def _check_college(db: Session, college_id: int | None) -> None:
    if college_id is not None and not db.get(College, college_id):
        raise ApiError(422, "validation_error", "Unknown college")


def _create_candidate(db: Session, body: CandidateIn) -> tuple[Candidate, str]:
    _check_college(db, body.college_id)
    pw = body.password or generate_password()
    user = User(email=body.email.lower(), password_hash=hash_password(pw), role=Role.candidate.value,
                must_change_password=True)
    db.add(user)
    db.flush()
    c = Candidate(user_id=user.id, name=body.name, college_id=body.college_id, branch=body.branch, batch=body.batch)
    db.add(c)
    db.flush()
    return c, pw


def ser_candidate(c: Candidate) -> dict:
    return {"id": c.id, "email": c.user.email, "name": c.name, "college": c.college.name if c.college else "",
            "college_id": c.college_id, "branch": c.branch,
            "last_login_at": c.user.last_login_at, "invited_at": c.user.invited_at,
            "batch": c.batch, "state": c.state, "is_active": c.user.is_active}


@router.post("/candidates", status_code=201)
def create_candidate(body: CandidateIn, request: Request, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """Single candidate. The generated password (if none given) is returned ONCE."""
    if db.query(User).filter(User.email == body.email.lower()).first():
        raise ApiError(409, "email_taken", "Email already registered")
    c, pw = _create_candidate(db, body)
    audit(db, admin.id, "candidate_create", "candidate", c.id, ip=client_ip(request))
    db.commit()
    return ok({**ser_candidate(c), "password": pw})


@router.post("/candidates/bulk", status_code=201)
async def bulk_create_candidates(request: Request, file: UploadFile = File(...),
                                 admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """CSV columns: email,name,college,branch,batch. `college` is the onboarded college's exact name or code
    (blank allowed). Passwords are generated and returned ONCE."""
    rows = csvio.parse_csv(csvio.read_upload(await file.read()))
    if len(rows) > 2000:
        raise ApiError(413, "too_many_rows", "Max 2000 candidates per upload")
    adapter = TypeAdapter(CandidateIn)
    created, skipped, seen = [], [], set()
    lookup = {}
    for col in db.query(College):
        lookup[col.name.lower()] = col.id
        lookup[col.code.lower()] = col.id
    for i, row in enumerate(rows, start=1):
        cname = (row.get("college") or "").strip()
        if cname and cname.lower() not in lookup:  # data quality: never silently invent a college
            skipped.append({"row": i, "email": row.get("email"), "reason": f"unknown college '{cname}' (use the exact name or code)"})
            continue
        try:
            cin = adapter.validate_python({**{k: row.get(k, "") for k in ("email", "name", "branch", "batch")},
                                           "college_id": lookup.get(cname.lower()) if cname else None})
        except ValidationError as e:
            skipped.append({"row": i, "email": row.get("email"), "reason": e.errors()[0]["msg"]})
            continue
        em = cin.email.lower()
        if em in seen or db.query(User).filter(User.email == em).first():
            skipped.append({"row": i, "email": em, "reason": "email already exists"})
            continue
        seen.add(em)
        c, pw = _create_candidate(db, cin)
        created.append({**ser_candidate(c), "password": pw})
    audit(db, admin.id, "candidates_bulk_create", detail={"created": len(created), "skipped": len(skipped)},
          ip=client_ip(request))
    db.commit()
    return ok({"created": created, "skipped": skipped})


@router.post("/candidates/assign")
def assign_tests(body: AssignIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    test = db.get(Test, body.test_id)
    if not test:
        raise ApiError(404, "not_found", "Test not found")
    q = db.query(Candidate)
    if body.candidate_ids or body.batch:
        conds = []
        if body.candidate_ids:
            conds.append(Candidate.id.in_(body.candidate_ids))
        if body.batch:
            conds.append(Candidate.batch == body.batch)
        q = q.filter(or_(*conds))
    else:
        raise ApiError(422, "validation_error", "Give candidate_ids and/or batch")
    assigned = skipped = 0
    for c in q.all():
        existing = db.query(CandidateTest).filter_by(candidate_id=c.id, stage=test.stage).first()
        if existing:  # one test per stage per candidate (gating relies on it)
            skipped += 1
            continue
        db.add(CandidateTest(candidate_id=c.id, test_id=test.id, stage=test.stage))
        assigned += 1
    audit(db, admin.id, "tests_assign", "test", test.id, {"assigned": assigned, "skipped": skipped},
          ip=client_ip(request))
    db.commit()
    return ok({"assigned": assigned, "skipped_already_has_stage": skipped})


OUTCOME_STATES = {"cleared": ["FINAL_RESULT"], "not_cleared": ["CANDIDATE_REJECTED"],
                  "disqualified": ["DISQUALIFIED"], "not_started": ["REGISTERED"]}
STAGES = [s.value for s in STAGE_ORDER]


def outcome_of(state: str) -> str:
    for k, v in OUTCOME_STATES.items():
        if state in v:
            return k
    return "in_progress"


def _filtered(db: Session, search, batch, college_id, branch, state, outcome, stage, stage_result, test_id,
              ignore_outcome=False, access=None):
    q = db.query(Candidate).join(User)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Candidate.name.ilike(like), User.email.ilike(like)))
    for col, val in ((Candidate.batch, batch), (Candidate.college_id, college_id), (Candidate.branch, branch)):
        if val:
            q = q.filter(col == val)
    if state:
        q = q.filter(Candidate.state == state)
    if outcome and not ignore_outcome:
        if outcome == "in_progress":
            known = [x for v in OUTCOME_STATES.values() for x in v]
            q = q.filter(Candidate.state.notin_(known))
        else:
            q = q.filter(Candidate.state.in_(OUTCOME_STATES[outcome]))
    if access == "never":
        q = q.filter(User.last_login_at.is_(None))
    elif access == "signed_in":
        q = q.filter(User.last_login_at.isnot(None))
    if test_id:
        q = q.filter(Candidate.id.in_(db.query(CandidateTest.candidate_id).filter(CandidateTest.test_id == test_id)))
    if stage:
        sub = db.query(CandidateTest.candidate_id).outerjoin(Attempt, Attempt.candidate_test_id == CandidateTest.id) \
            .filter(CandidateTest.stage == stage)
        if stage_result == "cleared":
            sub = sub.filter(Attempt.passed.is_(True))
        elif stage_result == "not_cleared":
            sub = sub.filter(Attempt.status != "active", Attempt.passed.is_(False))
        elif stage_result == "in_progress":
            sub = sub.filter(Attempt.status == "active")
        elif stage_result == "not_started":
            sub = sub.filter(Attempt.id.is_(None))
        q = q.filter(Candidate.id.in_(sub))
    return q


def _stage_cells(db: Session, cands: list[Candidate]) -> dict[int, dict]:
    rows = (db.query(CandidateTest, Attempt).outerjoin(Attempt, Attempt.candidate_test_id == CandidateTest.id)
            .filter(CandidateTest.candidate_id.in_([c.id for c in cands] or [0])).all())
    out: dict[int, dict] = {c.id: {s: {"status": "not_assigned"} for s in STAGES} for c in cands}
    for ct, a in rows:
        if a is None:
            cell = {"status": "assigned", "test_id": ct.test_id}
        elif a.status == "active":
            cell = {"status": "in_progress", "test_id": ct.test_id}
        else:
            st = "disqualified" if a.status == "disqualified" else ("cleared" if a.passed else "not_cleared")
            cell = {"status": st, "test_id": ct.test_id, "score": a.score, "max_score": a.max_score,
                    "violations": a.violation_count, "submit_reason": a.submit_reason}
        out[ct.candidate_id][ct.stage] = cell
    return out


def ser_candidate_row(c: Candidate, cells: dict) -> dict:
    d = ser_candidate(c)
    d["outcome"] = outcome_of(c.state)
    d["stages"] = cells
    d["violations"] = sum(x.get("violations") or 0 for x in cells.values())
    d["total_score"] = round(sum(x.get("score") or 0 for x in cells.values()), 2)
    return d


FILTER_PARAMS = ("search", "batch", "college", "branch", "state", "outcome", "stage", "stage_result", "test_id")


@router.get("/candidates")
def list_candidates(search: str | None = None, batch: str | None = None, college_id: int | None = None,
                    branch: str | None = None, state: str | None = None,
                    outcome: str | None = Query(None, pattern="^(cleared|not_cleared|in_progress|not_started|disqualified)$"),
                    stage: str | None = Query(None, pattern="^(aptitude|behaviour|coding)$"),
                    stage_result: str | None = Query(None, pattern="^(cleared|not_cleared|in_progress|not_started)$"),
                    test_id: int | None = None,
                    access: str | None = Query(None, pattern="^(never|signed_in)$"),
                    sort: str = Query("newest", pattern="^(newest|name|score|violations)$"),
                    pp=Depends(page_params), _: User = Depends(require_admin), db: Session = Depends(get_db)):
    page, size = pp
    args = (search, batch, college_id, branch, state, outcome, stage, stage_result, test_id)
    q = _filtered(db, *args, access=access).options(selectinload(Candidate.user), selectinload(Candidate.college))
    total = q.count()
    # counts per outcome for the tiles, ignoring the outcome filter itself so tiles stay clickable
    base = _filtered(db, *args, ignore_outcome=True, access=access)
    counts = {k: 0 for k in ("cleared", "not_cleared", "in_progress", "not_started", "disqualified")}
    for st, n in base.with_entities(Candidate.state, func.count(Candidate.id)).group_by(Candidate.state).all():
        counts[outcome_of(st)] += n
    counts["all"] = sum(counts.values())
    counts["never_signed_in"] = base.filter(User.last_login_at.is_(None)).count()   # who still needs an invitation
    if sort in ("score", "violations"):  # computed per row: sort the filtered ids in Python, then page
        ids = [c.id for c in q.all()]
        cells_all = _stage_cells(db, [type("X", (), {"id": i}) for i in ids])
        key = (lambda i: -sum(x.get("score") or 0 for x in cells_all[i].values())) if sort == "score" \
            else (lambda i: -sum(x.get("violations") or 0 for x in cells_all[i].values()))
        ids.sort(key=key)
        page_ids = ids[(page - 1) * size: page * size]
        by_id = {c.id: c for c in db.query(Candidate).options(selectinload(Candidate.user), selectinload(Candidate.college)).filter(Candidate.id.in_(page_ids))}
        rows = [by_id[i] for i in page_ids]
    else:
        order = Candidate.name.asc() if sort == "name" else Candidate.id.desc()
        rows = q.order_by(order).offset((page - 1) * size).limit(size).all()
    cells = _stage_cells(db, rows)
    return ok([ser_candidate_row(c, cells[c.id]) for c in rows],
              {"page": page, "page_size": size, "total": total, "counts": counts})


@router.get("/candidates/filters")
def candidate_filter_options(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    def distinct(col):
        return sorted(v for (v,) in db.query(col).distinct().all() if v)
    tests = db.query(Test.id, Test.title, Test.stage).order_by(Test.id).all()
    return ok({"batches": distinct(Candidate.batch), "colleges": [{"id": i, "name": n} for i, n in db.query(College.id, College.name).order_by(College.name)],
               "branches": distinct(Candidate.branch),
               "tests": [{"id": i, "title": t, "stage": s} for i, t, s in tests]})


@router.get("/candidates/export")
def export_candidates(search: str | None = None, batch: str | None = None, college_id: int | None = None,
                      branch: str | None = None, state: str | None = None, outcome: str | None = None,
                      stage: str | None = None, stage_result: str | None = None, test_id: int | None = None,
                      admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """The candidate list as CSV, honouring every filter on the screen."""
    cands = (_filtered(db, search, batch, college_id, branch, state, outcome, stage, stage_result, test_id)
             .options(selectinload(Candidate.user), selectinload(Candidate.college)).order_by(Candidate.name).limit(10000).all())
    cells = _stage_cells(db, cands)
    rows = []
    for c in cands:
        r = [c.id, c.name, c.user.email, c.college.name if c.college else "", c.branch, c.batch, outcome_of(c.state), c.state]
        for s in STAGES:
            x = cells[c.id][s]
            r += [x["status"], x.get("score", ""), x.get("max_score", "")]
        r.append(sum(x.get("violations") or 0 for x in cells[c.id].values()))
        rows.append(r)
    head = ["candidate_id", "name", "email", "college", "branch", "batch", "outcome", "state"]
    for s in STAGES:
        head += [f"{s}_result", f"{s}_score", f"{s}_max"]
    audit(db, admin.id, "candidates_export", detail={"rows": len(rows)})
    db.commit()
    return Response(csvio.to_csv(head + ["violations"], rows), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="candidates.csv"'})


@router.get("/candidates/{cid}/results")
def candidate_results(cid: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Candidate).options(selectinload(Candidate.user), selectinload(Candidate.college)).filter_by(id=cid).first()
    if not c:
        raise ApiError(404, "not_found", "Candidate not found")
    cts = (db.query(CandidateTest).options(selectinload(CandidateTest.attempt).selectinload(Attempt.violations),
                                           selectinload(CandidateTest.test))
           .filter_by(candidate_id=cid).all())
    cts.sort(key=lambda x: [s.value for s in STAGE_ORDER].index(x.stage))
    stages = []
    for ct in cts:
        a = ct.attempt
        entry = {"test_id": ct.test_id, "title": ct.test.title, "stage": ct.stage,
                 "cutoff": ct.test.cutoff_score, "attempt": None}
        if a:
            ans = {x.question_id: x for x in db.query(Answer).filter_by(attempt_id=a.id)}
            subs = db.query(CodeSubmission).filter_by(attempt_id=a.id).order_by(CodeSubmission.id).all()
            breakdown = []
            served = set(svc.attempt_question_ids(db, a))
            for q in db.query(Question).filter_by(test_id=ct.test_id).order_by(Question.id):
                if q.id not in served:
                    continue
                x = ans.get(q.id)
                row = {"question_id": q.id, "type": q.type, "body": q.body, "marks": q.marks}
                if q.type != "coding":
                    row.update(selected=x.selected_option if x else None, correct_option=q.correct_option,
                               is_correct=x.is_correct if x else None, score=x.score_awarded if x else 0)
                    row["selected_text"] = (q.options_json[x.selected_option]["text"]
                                            if x and x.selected_option is not None else None)
                breakdown.append(row)
            entry["attempt"] = {
                "id": a.id, "status": a.status, "score": a.score, "max_score": a.max_score, "passed": a.passed,
                "started_at": a.started_at, "submitted_at": a.submitted_at, "submit_reason": a.submit_reason,
                "violation_count": a.violation_count, "start_ip": a.start_ip, "breakdown": breakdown,
                "code_submissions": [{"id": s.id, "question_id": s.question_id, "language": s.language,
                                      "kind": s.kind, "status": s.status, "passed_cases": s.passed_cases,
                                      "total_cases": s.total_cases, "runtime_ms": s.runtime_ms,
                                      "memory_kb": s.memory_kb, "created_at": s.created_at,
                                      "source_code": s.source_code} for s in subs],
                "violations": [{"type": v.type, "at": v.occurred_at, "counted": v.counted,
                                "meta": v.client_meta} for v in a.violations],
                "personality": personality.profile(db, a, ct.test_id) if ct.stage == "behaviour" else None}
        stages.append(entry)
    beh = next((e["attempt"] for e in stages if e["stage"] == "behaviour" and e["attempt"]), None)
    return ok({"candidate": ser_candidate(c), "stages": stages,
               "insights": insights.build(db, cts, beh["personality"] if beh else None)})


@router.get("/results/export")
def export_results(test_id: int, passed_only: bool = False, min_score: float | None = Query(None, ge=0),
                   admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """CSV export; `passed_only` / `min_score` give the shortlist. Formula-injection safe."""
    test = db.get(Test, test_id)
    if not test:
        raise ApiError(404, "not_found", "Test not found")
    cts = (db.query(CandidateTest).options(selectinload(CandidateTest.attempt),
                                           selectinload(CandidateTest.candidate).selectinload(Candidate.user),
                                           selectinload(CandidateTest.candidate).selectinload(Candidate.college))
           .filter_by(test_id=test_id).all())
    rows = []
    for ct in cts:
        a = ct.attempt
        if passed_only and not (a and a.passed):
            continue
        if min_score is not None and not (a and a.score is not None and a.score >= min_score):
            continue
        c = ct.candidate
        rows.append([c.id, c.name, c.user.email, c.college.name if c.college else "", c.branch, c.batch, c.state,
                     a.status if a else "not_started", a.score if a else "", a.max_score if a else "",
                     "" if not a or a.passed is None else ("yes" if a.passed else "no"),
                     a.violation_count if a else "", a.submitted_at.isoformat() if a and a.submitted_at else ""])
    rows.sort(key=lambda r: (r[8] == "", -(r[8] or 0) if r[8] != "" else 0))
    audit(db, admin.id, "results_export", "test", test_id, {"rows": len(rows)})
    db.commit()
    body = csvio.to_csv(["candidate_id", "name", "email", "college", "branch", "batch", "state", "attempt_status",
                         "score", "max_score", "passed", "violations", "submitted_at"], rows)
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="results_test_{test_id}.csv"'})


@router.post("/candidates/{cid}/disqualify")
def disqualify(cid: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Candidate).options(selectinload(Candidate.user)).filter_by(id=cid).first()
    if not c:
        raise ApiError(404, "not_found", "Candidate not found")
    svc.disqualify(db, c)
    audit(db, admin.id, "candidate_disqualify", "candidate", cid, ip=client_ip(request))
    db.commit()
    return ok(ser_candidate(c))


@router.get("/audit")
def audit_log(action: str | None = None, pp=Depends(page_params), _: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    page, size = pp
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    rows = q.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return ok([{"id": r.id, "actor_id": r.actor_id, "action": r.action, "entity": r.entity,
                "entity_id": r.entity_id, "detail": r.detail, "at": r.at, "ip": r.ip} for r in rows],
              {"page": page, "page_size": size, "total": total})


# ------------------------------------------------------------------ edit / delete
def _load(db: Session, cid: int) -> Candidate:
    c = db.query(Candidate).options(selectinload(Candidate.user), selectinload(Candidate.college)).filter_by(id=cid).first()
    if not c:
        raise ApiError(404, "not_found", "Candidate not found")
    return c


@router.put("/candidates/{cid}")
def update_candidate(cid: int, body: CandidateUpdate, request: Request, admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    c = _load(db, cid)
    email = body.email.lower()
    if email != c.user.email and db.query(User).filter(User.email == email).first():
        raise ApiError(409, "email_taken", "Email already registered")
    c.user.email, c.user.is_active = email, body.is_active
    _check_college(db, body.college_id)
    c.name, c.branch, c.batch = body.name, body.branch, body.batch
    c.college = db.get(College, body.college_id) if body.college_id else None  # keep relationship and FK in sync
    if not body.is_active:  # deactivating signs them out everywhere
        db.query(RefreshToken).filter(RefreshToken.user_id == c.user_id).update({"revoked": True})
    audit(db, admin.id, "candidate_update", "candidate", cid, {"active": body.is_active}, ip=client_ip(request))
    db.commit()
    return ok({**ser_candidate(c), "is_active": c.user.is_active})


@router.post("/candidates/{cid}/reset-password")
def reset_password(cid: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    c = _load(db, cid)
    pw = generate_password()
    c.user.password_hash = hash_password(pw)
    c.user.must_change_password = True
    c.user.failed_logins, c.user.locked_until = 0, None
    db.query(RefreshToken).filter(RefreshToken.user_id == c.user_id).update({"revoked": True})
    audit(db, admin.id, "candidate_reset_password", "candidate", cid, ip=client_ip(request))
    db.commit()
    return ok({"email": c.user.email, "password": pw})  # shown once


def _delete_candidates(db: Session, ids: list[int], admin: User, request: Request) -> int:
    n = 0
    for cid in ids:
        c = db.query(Candidate).options(selectinload(Candidate.user)).filter_by(id=cid).first()
        if not c:
            continue
        email = c.user.email
        db.delete(c.user)  # cascades candidate -> assignments -> attempts -> answers/violations/code
        audit(db, admin.id, "candidate_delete", "candidate", cid, {"email": email}, ip=client_ip(request))
        n += 1
    db.commit()
    return n


@router.delete("/candidates/{cid}")
def delete_candidate(cid: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _load(db, cid)
    return ok({"deleted": _delete_candidates(db, [cid], admin, request)})


@router.post("/candidates/bulk-delete")
def bulk_delete(body: BulkIds, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return ok({"deleted": _delete_candidates(db, body.ids, admin, request)})


@router.delete("/candidates/{cid}/tests/{test_id}")
def unassign_test(cid: int, test_id: int, request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    c = _load(db, cid)
    ct = db.query(CandidateTest).filter_by(candidate_id=cid, test_id=test_id).first()
    if not ct:
        raise ApiError(404, "not_found", "Test is not assigned to this candidate")
    if ct.attempt:
        raise ApiError(409, "attempt_exists", "Candidate already attempted this test; allow a retake first")
    db.delete(ct)
    db.flush()
    svc.recompute_state(db, c)
    audit(db, admin.id, "test_unassign", "candidate", cid, {"test_id": test_id}, ip=client_ip(request))
    db.commit()
    return ok({"unassigned": True})


@router.post("/candidates/{cid}/tests/{test_id}/retake")
def allow_retake(cid: int, test_id: int, request: Request, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """Admin override of the one-attempt rule: removes that attempt (answers, violations, code) so the
    candidate can start again. Later-stage attempts stay, so reset those first if the stage changes outcome."""
    c = _load(db, cid)
    ct = db.query(CandidateTest).filter_by(candidate_id=cid, test_id=test_id).first()
    if not ct or not ct.attempt:
        raise ApiError(404, "not_found", "No attempt to reset")
    info = {"test_id": test_id, "status": ct.attempt.status, "score": ct.attempt.score}
    db.delete(ct.attempt)
    ct.status = "assigned"
    db.flush()
    db.refresh(ct)
    svc.recompute_state(db, c)
    if c.state == "DISQUALIFIED" and info["status"] == "disqualified":
        c.state = "REGISTERED"  # the disqualification came from this attempt
        svc.recompute_state(db, c)
    audit(db, admin.id, "attempt_reset", "candidate", cid, info, ip=client_ip(request))
    db.commit()
    return ok({"reset": True, "state": c.state})


@router.post("/candidates/{cid}/reinstate")
def reinstate(cid: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Undo a manual disqualification; the state is re-derived from the attempts that remain."""
    c = _load(db, cid)
    if c.state != "DISQUALIFIED":
        raise ApiError(409, "not_disqualified", "Candidate is not disqualified")
    c.state = "REGISTERED"
    db.flush()
    svc.recompute_state(db, c)
    audit(db, admin.id, "candidate_reinstate", "candidate", cid, ip=client_ip(request))
    db.commit()
    return ok(ser_candidate(c))


# ------------------------------------------------------------------ invitations
@router.post("/candidates/invite")
def invite_candidates(body: InviteIn, request: Request, tasks: BackgroundTasks, admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """Emails each candidate a link to set their own password (no password is ever emailed). Safe to repeat: a new
    link replaces the old one. Delivery is in the background; with MAIL_BACKEND=console links are only logged."""
    cfg = get_settings()
    if not body.ids and not body.all_pending:
        raise ApiError(422, "validation_error", "Choose candidates, or all who have not signed in yet")
    q = db.query(Candidate).options(selectinload(Candidate.user)).join(User)
    q = q.filter(User.last_login_at.is_(None)) if body.all_pending and not body.ids else q.filter(Candidate.id.in_(body.ids))
    sent = skipped = 0
    for c in q.limit(2000).all():
        if not c.user.is_active:
            skipped += 1
            continue
        raw = issue_token(db, c.user, "invite", timedelta(days=cfg.invite_token_days))
        c.user.invited_at = utcnow()
        tasks.add_task(mail.send, mail.invite_email(c.user.email, c.name, f"{cfg.public_url}/reset?token={raw}", cfg.invite_token_days,
                                                    f"{cfg.public_url}/login", f"{cfg.public_url}/help"))
        sent += 1
    audit(db, admin.id, "candidates_invite", detail={"sent": sent, "skipped_inactive": skipped}, ip=client_ip(request))
    db.commit()
    return ok({"queued": sent, "skipped_inactive": skipped, "backend": cfg.mail_backend,
               "warning": "Email is in console mode: invitation links are written to the server log, nothing is delivered."
               if cfg.mail_backend == "console" else None})
