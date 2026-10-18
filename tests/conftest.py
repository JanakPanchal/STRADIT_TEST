import os

os.environ.update(JWT_SECRET="test-secret-test-secret-test-secret", RATE_LIMIT_ENABLED="false",
                  ADMIN_PASSWORD="AdminPass123", DATABASE_URL="sqlite:///./test_unused.db", MAIL_BACKEND="memory",
                  PUBLIC_URL="http://testserver")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, get_db
from app.main import app
from app.models import Role, User
from app.services.judge0 import CaseResult, get_judge


class FakeJudge:
    """Stands in for Judge0: a program 'passes' a case if its source contains the expected output."""
    def judge(self, language, source, cases, tl, mem):
        out = []
        for stdin, exp in cases:
            if exp is None:  # custom run: no expected output, just echo what "ran"
                out.append(CaseResult(passed=True, status="Accepted", time_ms=7, memory_kb=900, stdout=f"ran:{stdin}"))
            else:
                ok = exp.strip() in source
                out.append(CaseResult(passed=ok, status="Accepted" if ok else "Wrong Answer", time_ms=5, memory_kb=1000, stdout=exp))
        return out


@pytest.fixture()
def db_session():
    # Default: in-memory SQLite. Set TEST_DATABASE_URL to run the SAME suite on PostgreSQL (use a throw-away DB:
    # tables are dropped after every test).
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = create_engine(url, pool_size=20, max_overflow=30)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    SessionLocal.configure(bind=engine)
    with TestSession() as s:
        s.add(User(email="admin@example.com", password_hash=hash_password("AdminPass123"), role=Role.admin.value))
        s.commit()

    def _get_db():
        with TestSession() as s:
            yield s
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_judge] = lambda: FakeJudge()
    yield TestSession
    app.dependency_overrides.clear()
    if url:
        engine.dispose()
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    from app.services import mail
    mail.OUTBOX.clear()
    with TestClient(app) as c:
        yield c


def login(client, email, password):
    r = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture()
def admin(client):
    return login(client, "admin@example.com", "AdminPass123")


def new_client():
    return TestClient(app)


def make_candidate(client, admin, email="c1@x.com", batch="B1"):
    r = client.post("/api/v1/admin/candidates", headers=admin,
                    json={"email": email, "name": email.split("@")[0], "batch": batch, "password": "Password123"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def make_test(client, admin, stage, cutoff=1, duration=30, **kw):
    r = client.post("/api/v1/admin/tests", headers=admin, json={
        "title": f"{stage} test", "stage": stage, "duration_min": duration, "cutoff_score": cutoff, **kw})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def mcq(body="2+2?", opts=("3", "4", "5", "6"), correct=1, marks=1):
    return {"type": "mcq", "body": body, "options": [{"text": o} for o in opts], "correct_option": correct,
            "marks": marks}


def add_q(client, admin, tid, q):
    r = client.post(f"/api/v1/admin/tests/{tid}/questions", headers=admin, json=q)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def assign(client, admin, tid, cid):
    r = client.post("/api/v1/admin/candidates/assign", headers=admin, json={"test_id": tid, "candidate_ids": [cid]})
    assert r.status_code == 200, r.text
