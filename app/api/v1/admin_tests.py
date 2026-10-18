from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.deps import client_ip, require_admin
from app.core.errors import ApiError, ok
from app.db.session import get_db
from app.models import Attempt, Candidate, CandidateTest, Question, QType, Test, TestCase, User
from app.schemas.schemas import QuestionIn, TestIn
from app.services import csvio
from app.services import analytics, attempts as svc
from app.services.audit import audit

router = APIRouter(prefix="/admin/tests", tags=["admin-tests"])


def page_params(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return page, page_size


def ser_test(t: Test, qcount: int | None = None) -> dict:
    return {"id": t.id, "title": t.title, "stage": t.stage, "duration_min": t.duration_min,
            "start_at": t.start_at, "end_at": t.end_at, "cutoff_score": t.cutoff_score,
            "max_violations": t.max_violations, "show_result": t.show_result,
            "questions_per_attempt": t.questions_per_attempt, "is_active": t.is_active, "question_count": qcount}


def ser_question(q: Question) -> dict:
    return {"id": q.id, "test_id": q.test_id, "type": q.type, "body": q.body, "options": q.options_json,
            "correct_option": q.correct_option, "marks": q.marks, "difficulty": q.difficulty,
            "input_format": q.input_format, "output_format": q.output_format,
            "function_signature": q.function_signature, "time_limit_ms": q.time_limit_ms,
            "memory_limit_kb": q.memory_limit_kb,
            "test_cases": [{"id": c.id, "input": c.input, "expected_output": c.expected_output,
                            "is_sample": c.is_sample} for c in q.test_cases]}


def _get_test(db: Session, test_id: int) -> Test:
    t = db.get(Test, test_id)
    if not t:
        raise ApiError(404, "not_found", "Test not found")
    return t


def question_fields(test: Test, body: QuestionIn) -> tuple[dict, list[TestCase]]:
    """Validated column values + fresh TestCase rows. Deliberately builds NO Question object: a throw-away
    Question that shares TestCase objects with a persistent one gets persisted by SQLAlchemy's cascades."""
    if body.type.value != "coding" and test.stage == "coding":
        raise ApiError(400, "stage_mismatch", "Coding tests only accept coding questions")
    if body.type.value == "coding" and test.stage != "coding":
        raise ApiError(400, "stage_mismatch", "Coding questions belong to a coding-stage test")
    if body.type.value == "mcq" and test.stage == "behaviour":
        raise ApiError(400, "stage_mismatch", "Behaviour tests use likert questions")
    if body.type.value == "likert" and test.stage == "aptitude":
        raise ApiError(400, "stage_mismatch", "Aptitude tests use mcq questions")
    opts = [{"text": o.text, **({"score": o.score} if o.score is not None else {})} for o in body.options] \
        if body.options else None
    fields = dict(type=body.type.value, body=body.body, options_json=opts,
                  correct_option=body.correct_option if body.type == QType.mcq else None,
                  marks=body.marks, difficulty=body.difficulty, input_format=body.input_format,
                  output_format=body.output_format, function_signature=body.function_signature,
                  time_limit_ms=body.time_limit_ms, memory_limit_kb=body.memory_limit_kb)
    cases = [TestCase(input=c.input, expected_output=c.expected_output, is_sample=c.is_sample) for c in body.test_cases]
    return fields, cases


def build_question(test: Test, body: QuestionIn) -> Question:
    fields, cases = question_fields(test, body)
    q = Question(test_id=test.id, **fields)
    q.test_cases = cases
    return q


@router.post("", status_code=201)
def create_test(body: TestIn, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = Test(**{**body.model_dump(mode="python"), "stage": body.stage.value})
    db.add(t)
    db.flush()
    audit(db, admin.id, "test_create", "test", t.id, ip=client_ip(request))
    db.commit()
    return ok(ser_test(t, 0))


@router.get("")
def list_tests(stage: str | None = None, pp=Depends(page_params), _: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    page, size = pp
    q = db.query(Test)
    if stage:
        q = q.filter(Test.stage == stage)
    total = q.count()
    tests = q.order_by(Test.id.desc()).offset((page - 1) * size).limit(size).all()
    ids = [t.id for t in tests]
    counts = dict(db.query(Question.test_id, func.count(Question.id))
                  .filter(Question.test_id.in_(ids)).group_by(Question.test_id).all())
    assigned = dict(db.query(CandidateTest.test_id, func.count(CandidateTest.id))
                    .filter(CandidateTest.test_id.in_(ids)).group_by(CandidateTest.test_id).all())
    attempted = dict(db.query(CandidateTest.test_id, func.count(Attempt.id)).join(Attempt, Attempt.candidate_test_id == CandidateTest.id)
                     .filter(CandidateTest.test_id.in_(ids)).group_by(CandidateTest.test_id).all())
    rows = []
    for t in tests:  # assigned/attempted feed the admin Overview setup checklist
        d = ser_test(t, counts.get(t.id, 0))
        d.update(assigned_count=assigned.get(t.id, 0), attempt_count=attempted.get(t.id, 0))
        rows.append(d)
    return ok(rows, {"page": page, "page_size": size, "total": total})


@router.get("/{test_id}")
def get_test(test_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    return ok(ser_test(t, db.query(Question).filter_by(test_id=t.id).count()))


@router.put("/{test_id}")
def update_test(test_id: int, body: TestIn, request: Request, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    started = db.query(Attempt).join(CandidateTest).filter(CandidateTest.test_id == t.id).count()
    if started and body.stage.value != t.stage:
        raise ApiError(409, "test_in_use", "Cannot change stage after attempts exist")
    # exclude_unset: a client that does not know a field (e.g. the pool size) cannot silently reset it
    for k, v in body.model_dump(mode="python", exclude_unset=True).items():
        setattr(t, k, v.value if hasattr(v, "value") else v)
    audit(db, admin.id, "test_update", "test", t.id, ip=client_ip(request))
    db.commit()
    return ok(ser_test(t))


@router.delete("/{test_id}")
def delete_test(test_id: int, request: Request, force: bool = Query(False), admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Without `force`, refuses when candidates already attempted (409 + counts so the UI can confirm).
    With `force=true` it removes the test, its questions AND every attempt/answer/violation under it."""
    t = _get_test(db, test_id)
    cts = db.query(CandidateTest).filter_by(test_id=t.id).all()
    attempts = db.query(Attempt).join(CandidateTest).filter(CandidateTest.test_id == t.id).count()
    if attempts and not force:
        raise ApiError(409, "test_in_use", f"{attempts} attempt(s) exist for this test",
                       {"attempts": attempts, "assigned": len(cts)})
    cands = [db.get(Candidate, ct.candidate_id) for ct in cts]
    for ct in cts:
        db.delete(ct)  # cascades attempt -> answers, violations, code submissions
    db.flush()
    db.delete(t)
    db.flush()
    for c in cands:
        svc.recompute_state(db, c)
    audit(db, admin.id, "test_delete", "test", t.id, {"force": force, "attempts": attempts, "assigned": len(cts)},
          ip=client_ip(request))
    db.commit()
    return ok({"deleted": True, "attempts_removed": attempts})


# ------------------------------------------------------------------ questions
def _guard_edit(db: Session, test: Test) -> None:
    if db.query(Attempt).join(CandidateTest).filter(CandidateTest.test_id == test.id).count():
        raise ApiError(409, "test_in_use", "Questions are locked once candidates have started this test")


@router.get("/{test_id}/questions")
def list_questions(test_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    _get_test(db, test_id)
    qs = db.query(Question).options(selectinload(Question.test_cases)).filter_by(test_id=test_id).order_by(Question.id).all()
    return ok([ser_question(q) for q in qs])


@router.post("/{test_id}/questions", status_code=201)
def add_question(test_id: int, body: QuestionIn, request: Request, admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    _guard_edit(db, t)
    q = build_question(t, body)
    db.add(q)
    db.flush()
    audit(db, admin.id, "question_create", "question", q.id, ip=client_ip(request))
    db.commit()
    return ok(ser_question(q))


@router.put("/{test_id}/questions/{qid}")
def edit_question(test_id: int, qid: int, body: QuestionIn, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    _guard_edit(db, t)
    old = db.get(Question, qid)
    if not old or old.test_id != test_id:
        raise ApiError(404, "not_found", "Question not found")
    fields, cases = question_fields(t, body)
    for f, v in fields.items():
        setattr(old, f, v)
    old.test_cases = cases  # old cases are deleted (delete-orphan), the fresh ones inserted
    audit(db, admin.id, "question_update", "question", qid)
    db.commit()
    return ok(ser_question(old))


@router.delete("/{test_id}/questions/{qid}")
def delete_question(test_id: int, qid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    _guard_edit(db, t)
    q = db.get(Question, qid)
    if not q or q.test_id != test_id:
        raise ApiError(404, "not_found", "Question not found")
    audit(db, admin.id, "question_delete", "question", qid)
    db.delete(q)
    db.commit()
    return ok({"deleted": True})


def _csv_row_to_question(row: dict) -> dict:
    qtype = row.get("type") or "mcq"
    texts = [o.strip() for o in (row.get("options") or "").split("|") if o.strip()]
    d: dict = {"type": qtype, "body": row.get("body", ""), "difficulty": row.get("difficulty") or "medium"}
    if row.get("marks"):
        d["marks"] = float(row["marks"])
    if qtype == "likert":
        scores = [float(x) for x in (row.get("scores") or "").split("|") if x.strip()]
        if len(scores) != len(texts):
            raise ValueError("scores count must match options count")
        d["options"] = [{"text": t, "score": s} for t, s in zip(texts, scores)]
    else:
        d["options"] = [{"text": t} for t in texts]
        d["correct_option"] = int(row["correct"]) - 1 if row.get("correct") else None  # CSV is 1-based
    return d


@router.post("/{test_id}/questions/import", status_code=201)
async def import_questions(test_id: int, request: Request, file: UploadFile = File(...),
                           dry_run: bool = Query(False), admin: User = Depends(require_admin),
                           db: Session = Depends(get_db)):
    """CSV (columns: type,body,options,correct,marks,difficulty,scores; options '|'-separated,
    correct is 1-based) or JSON (list of question objects; the only way to import coding questions).
    All-or-nothing: any invalid row rejects the whole file with per-row errors."""
    t = _get_test(db, test_id)
    _guard_edit(db, t)
    text = csvio.read_upload(await file.read())
    is_json = (file.filename or "").lower().endswith(".json")
    raw_rows = csvio.parse_json(text) if is_json else csvio.parse_csv(text)
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ApiError(400, "empty_import", "No questions found in file")
    if len(raw_rows) > 1000:
        raise ApiError(413, "too_many_rows", "Max 1000 questions per import")
    parsed, errors = [], []
    for i, row in enumerate(raw_rows, start=1):
        try:
            parsed.append(QuestionIn.model_validate(row if is_json else _csv_row_to_question(row)))
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            msg = "; ".join(x["msg"] for x in e.errors()) if isinstance(e, ValidationError) else str(e)
            errors.append({"row": i, "error": msg})
    if errors:
        raise ApiError(422, "import_failed", "Some rows are invalid; nothing was imported", errors)
    try:
        qs = [build_question(t, p) for p in parsed]
    except ApiError as e:
        raise ApiError(422, "import_failed", e.message)
    if dry_run:  # Test Center preview: validate everything, save nothing
        return ok({"valid": True, "count": len(qs), "preview": [
            {"n": i + 1, "type": q.type, "body": q.body[:140], "marks": q.marks,
             "options": len(q.options_json or []), "test_cases": len(q.test_cases)} for i, q in enumerate(qs)]})
    db.add_all(qs)
    audit(db, admin.id, "questions_import", "test", t.id, {"count": len(qs)}, ip=client_ip(request))
    db.commit()
    return ok({"imported": len(qs)})


# ------------------------------------------------------------------ Test Center: summary / publish / clone
def question_max_marks(q: Question) -> float:
    if q.type == QType.likert.value:
        return max(o["score"] for o in q.options_json)
    return q.marks


def test_summary(db: Session, t: Test) -> dict:
    qs = db.query(Question).options(selectinload(Question.test_cases)).filter_by(test_id=t.id).all()
    total = round(sum(question_max_marks(q) for q in qs), 4)
    n = t.questions_per_attempt
    marks = sorted(question_max_marks(q) for q in qs)
    if n and qs:
        n_eff = min(n, len(qs))
        min_total, max_total = round(sum(marks[:n_eff]), 4), round(sum(marks[-n_eff:]), 4)  # worst / best possible draw
    else:
        min_total = max_total = total
    by_type = {"mcq": 0, "likert": 0, "coding": 0}
    by_diff = {"easy": 0, "medium": 0, "hard": 0}
    errors, warnings = [], []
    for q in qs:
        by_type[q.type] += 1
        by_diff[q.difficulty] = by_diff.get(q.difficulty, 0) + 1
        if q.type == QType.mcq.value:
            texts = [o["text"].strip().lower() for o in q.options_json]
            if len(set(texts)) != len(texts):
                warnings.append(f"Question #{q.id} has duplicate options")
    if not qs:
        errors.append("Add at least one question")
    if n and qs and n > len(qs):
        errors.append(f"Questions per candidate ({n}) is larger than the pool ({len(qs)})")
    if qs and t.cutoff_score > min_total:
        errors.append(f"Cutoff ({t.cutoff_score}) is higher than the {'smallest possible total for a candidate' if n else 'total marks'} "
                      f"({min_total}); some candidates could not pass")
    if n and qs and min_total != max_total:
        warnings.append(f"Questions carry different marks, so candidates can get a maximum between {min_total} and {max_total}. "
                        "Use equal marks within a pool for a fair comparison")
    if n and qs and len(qs) < n * 2:
        warnings.append(f"The pool is small ({len(qs)}) for {n} per candidate; candidates will see mostly the same questions. "
                        "Aim for at least 2-3x")
    if qs and t.cutoff_score == 0:
        warnings.append("Cutoff is 0: every candidate will clear this stage")
    if qs and t.stage != "coding" and t.duration_min * 60 / len(qs) < 20:
        warnings.append("Less than 20 seconds per question")
    if t.start_at and t.end_at and (t.end_at - t.start_at).total_seconds() < t.duration_min * 60:
        warnings.append("The window is shorter than the duration; late starters will be cut off")
    if t.stage == "coding":
        for q in qs:
            if sum(1 for c in q.test_cases if not c.is_sample) < 2:
                warnings.append(f"Coding question #{q.id} has fewer than 2 hidden test cases")
    return {"test": ser_test(t, len(qs)), "question_count": len(qs), "total_marks": total,
            "per_candidate": {"questions": min(n, len(qs)) if n else len(qs), "min_marks": min_total, "max_marks": max_total}, "by_type": by_type,
            "by_difficulty": by_diff, "errors": errors, "warnings": warnings, "ready": not errors}


@router.get("/{test_id}/summary")
def summary(test_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    return ok(test_summary(db, _get_test(db, test_id)))


@router.get("/{test_id}/analytics")
def test_analytics(test_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Outcome, score distribution, timing, item analysis and college/batch breakdown for one test."""
    return ok(analytics.test_analytics(db, _get_test(db, test_id)))


@router.post("/{test_id}/publish")
def publish(test_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    s = test_summary(db, t)
    if s["errors"]:
        raise ApiError(409, "not_ready", "Fix the issues before publishing", s["errors"])
    t.is_active = True
    audit(db, admin.id, "test_publish", "test", t.id, ip=client_ip(request))
    db.commit()
    return ok({**s, "test": ser_test(t, s["question_count"])})


@router.post("/{test_id}/unpublish")
def unpublish(test_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    t = _get_test(db, test_id)
    t.is_active = False  # hides it from candidates; running attempts continue
    audit(db, admin.id, "test_unpublish", "test", t.id, ip=client_ip(request))
    db.commit()
    return ok(ser_test(t))


@router.post("/{test_id}/clone", status_code=201)
def clone(test_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    src = _get_test(db, test_id)
    new = Test(title=f"{src.title} (copy)", stage=src.stage, duration_min=src.duration_min, start_at=None,
               end_at=None, cutoff_score=src.cutoff_score, max_violations=src.max_violations,
               questions_per_attempt=src.questions_per_attempt,
               show_result=src.show_result, is_active=False)  # copies start as drafts
    db.add(new)
    db.flush()
    for q in db.query(Question).options(selectinload(Question.test_cases)).filter_by(test_id=src.id).order_by(Question.id):
        nq = Question(test_id=new.id, type=q.type, body=q.body, options_json=q.options_json,
                      correct_option=q.correct_option, marks=q.marks, difficulty=q.difficulty,
                      input_format=q.input_format, output_format=q.output_format,
                      function_signature=q.function_signature, time_limit_ms=q.time_limit_ms,
                      memory_limit_kb=q.memory_limit_kb)
        nq.test_cases = [TestCase(input=c.input, expected_output=c.expected_output, is_sample=c.is_sample)
                         for c in q.test_cases]
        db.add(nq)
    audit(db, admin.id, "test_clone", "test", new.id, {"from": src.id}, ip=client_ip(request))
    db.commit()
    return ok(ser_test(new))
