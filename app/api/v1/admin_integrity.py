from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import ApiError, ok
from app.core.deps import require_admin
from app.db.session import get_db
from app.models import CodeSubmission, Test, User
from app.services import integrity

router = APIRouter(prefix="/admin", tags=["admin-integrity"])


@router.get("/tests/{test_id}/integrity")
def test_integrity(test_id: int, similarity: float = Query(0.8, ge=0.3, le=1.0), _: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Review aid, not a verdict: similar code, shared wrong answers, unusually fast high scores, warning bursts."""
    t = db.get(Test, test_id)
    if not t:
        raise ApiError(404, "not_found", "Test not found")
    flags, shared_ip = integrity.attempt_flags(db, t)
    return ok({"test": {"id": t.id, "title": t.title, "stage": t.stage},
               "attempt_flags": flags,
               "answer_pairs": integrity.answer_concordance(db, t.id) if t.stage != "coding" else [],
               "code_pairs": integrity.code_similarity(db, t.id, similarity) if t.stage == "coding" else [],
               "shared_ip": shared_ip,
               "notes": "Signals only. A shared IP is normal in a lab (one NAT address); shared wrong answers and "
                        "near-identical code deserve a human look."})


@router.get("/code-submissions/{sid}")
def code_submission(sid: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    s = db.get(CodeSubmission, sid)
    if not s:
        raise ApiError(404, "not_found", "Submission not found")
    return ok({"id": s.id, "language": s.language, "kind": s.kind, "status": s.status, "passed_cases": s.passed_cases,
               "total_cases": s.total_cases, "source_code": s.source_code, "created_at": s.created_at})
