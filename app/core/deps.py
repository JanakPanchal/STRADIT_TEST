import jwt
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Attempt, Candidate, CandidateTest, Role, User

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(token: str | None = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise ApiError(401, "not_authenticated", "Authentication required")
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise ApiError(401, "invalid_token", "Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    # re-read from DB each request: deactivated users lose access immediately
    if not user or not user.is_active:
        raise ApiError(401, "inactive_user", "Account disabled")
    return user


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_admin(request: Request, user: User = Depends(get_current_user)) -> User:
    """Staff access. Admins can do everything; viewers (invigilators, placement officers) can only read."""
    if user.role == Role.admin.value:
        return user
    if user.role == Role.viewer.value:
        if request.method in SAFE_METHODS:
            return user
        raise ApiError(403, "read_only", "Your account is view-only. Ask an administrator to make this change.")
    raise ApiError(403, "forbidden", "Admin access required")


def require_full_admin(user: User = Depends(get_current_user)) -> User:
    """Team management and other account-level actions: never available to view-only staff."""
    if user.role != Role.admin.value:
        raise ApiError(403, "forbidden", "Administrator access required")
    return user


def require_candidate(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Candidate:
    if user.role != Role.candidate.value or not user.candidate:
        raise ApiError(403, "forbidden", "Candidate access required")
    return user.candidate


def get_own_attempt(attempt_id: int, cand: Candidate = Depends(require_candidate),
                    db: Session = Depends(get_db)) -> Attempt:
    """Ownership check: candidate A can never reach candidate B's attempt (404, not 403, to avoid probing)."""
    att = (db.query(Attempt).join(CandidateTest, Attempt.candidate_test_id == CandidateTest.id)
           .filter(Attempt.id == attempt_id, CandidateTest.candidate_id == cand.id).first())
    if not att:
        raise ApiError(404, "not_found", "Attempt not found")
    return att
