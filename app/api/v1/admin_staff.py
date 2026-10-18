from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.orm import Session

from app.api.v1.auth import issue_token
from app.core.config import get_settings
from app.core.deps import client_ip, require_admin, require_full_admin
from app.core.errors import ApiError, ok
from app.core.security import generate_password, hash_password, utcnow
from app.db.session import get_db
from app.models import RefreshToken, Role, User
from app.schemas.schemas import StaffIn, StaffUpdate
from app.services import mail
from app.services.audit import audit

router = APIRouter(prefix="/admin/staff", tags=["admin-staff"])
STAFF = [Role.admin.value, Role.viewer.value]


def ser(u: User) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name or "", "role": u.role, "is_active": u.is_active,
            "last_login_at": u.last_login_at, "invited_at": u.invited_at}


def _active_admins(db: Session) -> int:
    return db.query(User).filter(User.role == Role.admin.value, User.is_active.is_(True)).count()


def _get(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if not u or u.role not in STAFF:
        raise ApiError(404, "not_found", "Team member not found")
    return u


def _send_invite(db: Session, tasks: BackgroundTasks, u: User, inviter: User) -> None:
    cfg = get_settings()
    raw = issue_token(db, u, "invite", timedelta(days=cfg.invite_token_days))
    u.invited_at = utcnow()
    tasks.add_task(mail.send, mail.staff_invite_email(u.email, u.full_name or "there", u.role, f"{cfg.public_url}/reset?token={raw}",
                                                      cfg.invite_token_days, f"{cfg.public_url}/login", inviter.full_name or inviter.email))


@router.get("")
def list_staff(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Visible to view-only staff too (read-only); every change below needs a full administrator."""
    rows = db.query(User).filter(User.role.in_(STAFF)).order_by(User.role, User.email).all()
    return ok([ser(u) for u in rows], {"active_admins": _active_admins(db)})


@router.post("", status_code=201)
def add_member(body: StaffIn, request: Request, tasks: BackgroundTasks, admin: User = Depends(require_full_admin),
               db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise ApiError(409, "email_taken", "That email already has an account")
    # nobody knows this password: the person sets their own through the emailed link
    u = User(email=email, full_name=body.full_name.strip(), role=body.role, password_hash=hash_password(generate_password(24)), must_change_password=False)
    db.add(u)
    db.flush()
    _send_invite(db, tasks, u, admin)
    audit(db, admin.id, "staff_add", "user", u.id, {"email": email, "role": body.role}, ip=client_ip(request))
    db.commit()
    return ok({**ser(u), "backend": get_settings().mail_backend})


@router.put("/{uid}")
def update_member(uid: int, body: StaffUpdate, request: Request, admin: User = Depends(require_full_admin), db: Session = Depends(get_db)):
    u = _get(db, uid)
    losing_admin = u.role == Role.admin.value and u.is_active and (body.role != Role.admin.value or not body.is_active)
    if losing_admin and u.id == admin.id:
        raise ApiError(409, "self_lockout", "You cannot demote or deactivate your own account. Ask another administrator.")
    if losing_admin and _active_admins(db) <= 1:
        raise ApiError(409, "last_admin", "This is the last active administrator. Add another one first.")
    u.full_name, u.role, u.is_active = body.full_name.strip(), body.role, body.is_active
    if not body.is_active:   # deactivation cuts them off at once (access tokens are re-checked on every request)
        db.query(RefreshToken).filter(RefreshToken.user_id == u.id).update({"revoked": True})
    audit(db, admin.id, "staff_update", "user", u.id, {"role": body.role, "active": body.is_active}, ip=client_ip(request))
    db.commit()
    return ok(ser(u))


@router.post("/{uid}/send-link")
def send_link(uid: int, request: Request, tasks: BackgroundTasks, admin: User = Depends(require_full_admin), db: Session = Depends(get_db)):
    """(Re)sends the set-your-password link: for a new member who lost the email, or a forgotten password."""
    u = _get(db, uid)
    if not u.is_active:
        raise ApiError(409, "inactive", "Reactivate this account first")
    _send_invite(db, tasks, u, admin)
    audit(db, admin.id, "staff_send_link", "user", u.id, ip=client_ip(request))
    db.commit()
    return ok({"sent": True, "backend": get_settings().mail_backend})


@router.delete("/{uid}")
def remove_member(uid: int, request: Request, admin: User = Depends(require_full_admin), db: Session = Depends(get_db)):
    u = _get(db, uid)
    if u.id == admin.id:
        raise ApiError(409, "self_lockout", "You cannot remove your own account.")
    if u.role == Role.admin.value and u.is_active and _active_admins(db) <= 1:
        raise ApiError(409, "last_admin", "This is the last active administrator.")
    audit(db, admin.id, "staff_remove", "user", u.id, {"email": u.email}, ip=client_ip(request))
    db.delete(u)
    db.commit()
    return ok({"deleted": True})
