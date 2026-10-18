from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_admin
from app.core.errors import ApiError, ok
from app.db.session import get_db
from app.models import Candidate, College, User
from app.schemas.schemas import CollegeIn
from app.services.audit import audit

admin = APIRouter(prefix="/admin/colleges", tags=["admin-colleges"])
public = APIRouter(prefix="/colleges", tags=["public"])


def ser(c: College, count: int | None = None) -> dict:
    return {"id": c.id, "name": c.name, "code": c.code, "city": c.city, "state": c.state, "address": c.address,
            "website": c.website, "contact_name": c.contact_name, "contact_email": c.contact_email,
            "contact_phone": c.contact_phone, "notes": c.notes, "is_active": c.is_active, "candidate_count": count}


def _unique(db: Session, body: CollegeIn, exclude_id: int | None = None) -> None:
    for col, val, label in ((College.name, body.name.strip(), "name"), (College.code, body.code, "code")):
        q = db.query(College).filter(func.lower(col) == val.lower())
        if exclude_id:
            q = q.filter(College.id != exclude_id)
        if q.first():
            raise ApiError(409, "college_exists", f"A college with this {label} already exists")


@public.get("")
def public_list(db: Session = Depends(get_db)):
    """Registration dropdown. Deliberately minimal: id + name of active colleges only (no contact details)."""
    rows = db.query(College.id, College.name).filter(College.is_active.is_(True)).order_by(College.name).all()
    return ok([{"id": i, "name": n} for i, n in rows])


@admin.get("")
def list_colleges(search: str | None = None, active: bool | None = None, page: int = Query(1, ge=1),
                  page_size: int = Query(100, ge=1, le=500), _: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    q = db.query(College)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(College.name.ilike(like), College.code.ilike(like), College.city.ilike(like)))
    if active is not None:
        q = q.filter(College.is_active.is_(active))
    total = q.count()
    rows = q.order_by(College.name).offset((page - 1) * page_size).limit(page_size).all()
    counts = dict(db.query(Candidate.college_id, func.count(Candidate.id))
                  .filter(Candidate.college_id.in_([c.id for c in rows] or [0])).group_by(Candidate.college_id).all())
    return ok([ser(c, counts.get(c.id, 0)) for c in rows], {"page": page, "page_size": page_size, "total": total})


@admin.post("", status_code=201)
def create_college(body: CollegeIn, request: Request, adm: User = Depends(require_admin), db: Session = Depends(get_db)):
    _unique(db, body)
    c = College(**{**body.model_dump(), "name": body.name.strip(), "contact_email": body.contact_email or ""})
    db.add(c)
    db.flush()
    audit(db, adm.id, "college_create", "college", c.id, {"name": c.name}, ip=client_ip(request))
    db.commit()
    return ok(ser(c, 0))


@admin.put("/{cid}")
def update_college(cid: int, body: CollegeIn, request: Request, adm: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    c = db.get(College, cid)
    if not c:
        raise ApiError(404, "not_found", "College not found")
    _unique(db, body, exclude_id=cid)
    for k, v in {**body.model_dump(), "name": body.name.strip(), "contact_email": body.contact_email or ""}.items():
        setattr(c, k, v)
    audit(db, adm.id, "college_update", "college", cid, ip=client_ip(request))
    db.commit()
    return ok(ser(c, db.query(Candidate).filter_by(college_id=cid).count()))


@admin.delete("/{cid}")
def delete_college(cid: int, request: Request, move_to: int | None = None, adm: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Refuses while candidates belong to it, unless `move_to` names another college to move them to."""
    c = db.get(College, cid)
    if not c:
        raise ApiError(404, "not_found", "College not found")
    n = db.query(Candidate).filter_by(college_id=cid).count()
    if n:
        if move_to is None:
            raise ApiError(409, "college_in_use", f"{n} candidate(s) belong to this college", {"candidates": n})
        if move_to == cid or not db.get(College, move_to):
            raise ApiError(422, "validation_error", "move_to must be a different, existing college")
        db.query(Candidate).filter_by(college_id=cid).update({"college_id": move_to})
    audit(db, adm.id, "college_delete", "college", cid, {"name": c.name, "moved": n, "to": move_to}, ip=client_ip(request))
    db.delete(c)
    db.commit()
    return ok({"deleted": True, "moved_candidates": n})
