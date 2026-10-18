import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Query, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import client_ip, get_current_user
from app.core.errors import ApiError, ok
from app.core.ratelimit import limiter
from app.core.security import (create_access_token, hash_password, hash_token, new_refresh_token,
                               utcnow, verify_password)
from app.db.session import get_db
from app.models import Candidate, College, PasswordToken, RefreshToken, Role, User
from app.schemas.schemas import ChangePasswordIn, ForgotIn, RegisterIn, ResetIn
from app.services import mail
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
COOKIE = "refresh_token"
COOKIE_PATH = "/api/v1/auth"
# verified against when the user doesn't exist, so response time doesn't reveal valid emails
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _issue(db: Session, user: User, response: Response, family_id: str | None = None) -> dict:
    raw, h = new_refresh_token()
    db.add(RefreshToken(user_id=user.id, family_id=family_id or uuid.uuid4().hex, token_hash=h,
                        expires_at=utcnow() + timedelta(days=settings.refresh_token_days)))
    response.set_cookie(COOKIE, raw, max_age=settings.refresh_token_days * 86400, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path=COOKIE_PATH)
    return {"access_token": create_access_token(user.id, user.role), "token_type": "bearer",
            "expires_in": settings.access_token_minutes * 60, "role": user.role,
            "must_change_password": user.must_change_password}


@router.post("/register", status_code=201)
@limiter.limit(settings.login_rate)
def register(request: Request, body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise ApiError(409, "email_taken", "Email already registered")
    # candidates pick from the colleges the admin has onboarded; required once any college exists
    active = db.query(College).filter(College.is_active.is_(True))
    if body.college_id is not None:
        if not active.filter(College.id == body.college_id).first():
            raise ApiError(422, "validation_error", "Please choose a college from the list")
    elif active.count():
        raise ApiError(422, "validation_error", "Please select your college")
    user = User(email=email, password_hash=hash_password(body.password), role=Role.candidate.value)
    db.add(user)
    db.flush()
    db.add(Candidate(user_id=user.id, name=body.name, college_id=body.college_id,
                     branch=body.branch, batch=body.batch))
    audit(db, user.id, "register", "user", user.id, ip=client_ip(request))
    db.commit()
    return ok({"id": user.id, "email": user.email})


@router.post("/login")
@limiter.limit(settings.login_rate)
def login(request: Request, response: Response, form: OAuth2PasswordRequestForm = Depends(),
          db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username.lower()).first()
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        raise ApiError(423, "account_locked", "Too many failed attempts. Try again later.")
    db.commit()  # release the DB connection: bcrypt (~250 ms CPU) must not hold a pooled connection
    good = verify_password(form.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not good or not user.is_active:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                user.failed_logins = 0
            audit(db, user.id, "login_failed", "user", user.id, ip=client_ip(request))
            db.commit()
        raise ApiError(401, "bad_credentials", "Incorrect email or password")
    user.failed_logins, user.locked_until = 0, None
    user.last_login_at = now
    data = _issue(db, user, response)
    audit(db, user.id, "login", "user", user.id, ip=client_ip(request))
    db.commit()
    return ok(data)


@router.post("/refresh")
@limiter.limit(settings.login_rate)
def refresh(request: Request, response: Response, db: Session = Depends(get_db),
            refresh_token: str | None = Cookie(default=None)):
    if not refresh_token:
        raise ApiError(401, "no_refresh_token", "Not logged in")
    rt = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(refresh_token)).first()
    if not rt:
        raise ApiError(401, "invalid_refresh_token", "Invalid session")
    if rt.revoked:
        # a rotated token was replayed => likely stolen; kill the whole family
        db.query(RefreshToken).filter(RefreshToken.family_id == rt.family_id).update({"revoked": True})
        audit(db, rt.user_id, "refresh_reuse_detected", "user", rt.user_id, ip=client_ip(request))
        db.commit()
        raise ApiError(401, "invalid_refresh_token", "Session revoked")
    user = db.get(User, rt.user_id)
    if rt.expires_at < utcnow() or not user or not user.is_active:
        raise ApiError(401, "invalid_refresh_token", "Session expired")
    rt.revoked = True
    data = _issue(db, user, response, family_id=rt.family_id)
    db.commit()
    return ok(data)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           refresh_token: str | None = Cookie(default=None)):
    if refresh_token:
        rt = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(refresh_token)).first()
        if rt:
            db.query(RefreshToken).filter(RefreshToken.family_id == rt.family_id).update({"revoked": True})
            audit(db, rt.user_id, "logout", "user", rt.user_id, ip=client_ip(request))
            db.commit()
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return ok({"logged_out": True})


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    c = user.candidate
    return ok({"id": user.id, "email": user.email, "role": user.role, "full_name": user.full_name,
               "must_change_password": user.must_change_password,
               "candidate": {"id": c.id, "name": c.name, "state": c.state} if c else None})


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise ApiError(400, "bad_password", "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # revoke all sessions
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})
    audit(db, user.id, "change_password", "user", user.id, ip=client_ip(request))
    db.commit()
    return ok({"changed": True})


# ------------------------------------------------------------------ forgot / reset / invitation links
def issue_token(db: Session, user: User, purpose: str, ttl: timedelta) -> str:
    """Creates a single-use link token (only its hash is stored) and revokes older unused ones of the same purpose."""
    db.query(PasswordToken).filter(PasswordToken.user_id == user.id, PasswordToken.purpose == purpose,
                                   PasswordToken.used_at.is_(None)).update({"used_at": utcnow()})
    raw, h = new_refresh_token()
    db.add(PasswordToken(user_id=user.id, purpose=purpose, token_hash=h, expires_at=utcnow() + ttl))
    return raw


def _valid_token(db: Session, raw: str) -> PasswordToken | None:
    t = db.query(PasswordToken).filter(PasswordToken.token_hash == hash_token(raw)).first()
    return t if t and t.used_at is None and t.expires_at > utcnow() else None


@router.post("/forgot")
@limiter.limit(settings.login_rate)
def forgot_password(request: Request, body: ForgotIn, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Always answers the same way, so it cannot be used to discover which emails are registered."""
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user and user.is_active:
        recent = db.query(PasswordToken).filter(
            PasswordToken.user_id == user.id, PasswordToken.purpose == "reset",
            PasswordToken.created_at > utcnow() - timedelta(seconds=settings.reset_cooldown_seconds)).first()
        if not recent:  # at most one email a minute per account: no mail-bombing a candidate
            name = user.candidate.name if user.candidate else "there"
            raw = issue_token(db, user, "reset", timedelta(minutes=settings.reset_token_minutes))
            audit(db, user.id, "password_reset_requested", "user", user.id, ip=client_ip(request))
            db.commit()
            tasks.add_task(mail.send, mail.reset_email(user.email, name, f"{settings.public_url}/reset?token={raw}", settings.reset_token_minutes))
    return ok({"message": "If that email is registered, a reset link is on its way. It is valid for "
                          f"{settings.reset_token_minutes} minutes."})


@router.get("/reset-check")
def reset_check(token: str = Query(min_length=20, max_length=200), db: Session = Depends(get_db)):
    t = _valid_token(db, token)
    return ok({"valid": bool(t), "purpose": t.purpose if t else None})


@router.post("/reset")
@limiter.limit(settings.login_rate)
def reset_password(request: Request, body: ResetIn, db: Session = Depends(get_db)):
    t = _valid_token(db, body.token)
    user = db.get(User, t.user_id) if t else None
    if not t or not user or not user.is_active:
        raise ApiError(400, "invalid_link", "This link is invalid or has expired. Request a new one.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.failed_logins, user.locked_until = 0, None
    t.used_at = utcnow()
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).update({"revoked": True})  # sign out everywhere
    audit(db, user.id, "password_reset_done" if t.purpose == "reset" else "invitation_accepted", "user", user.id, ip=client_ip(request))
    db.commit()
    return ok({"changed": True, "purpose": t.purpose})


@router.post("/logout-all")
def logout_everywhere(request: Request, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revokes every refresh token of this account (all browsers and devices). Access tokens expire within minutes."""
    n = db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False)).update({"revoked": True})
    audit(db, user.id, "logout_everywhere", "user", user.id, {"sessions": n}, ip=client_ip(request))
    db.commit()
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return ok({"signed_out_sessions": n})
