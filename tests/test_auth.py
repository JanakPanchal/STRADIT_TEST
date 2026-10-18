from tests.conftest import login, new_client

REG = {"email": "a@x.com", "password": "Password123", "name": "A"}


def test_register_login_me_and_envelope(client):
    r = client.post("/api/v1/auth/register", json={**REG, "email": "A@x.com"})
    assert r.status_code == 201 and r.json()["error"] is None
    h = login(client, "a@x.com", "Password123")
    me = client.get("/api/v1/auth/me", headers=h).json()["data"]
    assert me["role"] == "candidate" and me["candidate"]["state"] == "REGISTERED"


def test_duplicate_email_409_and_validation_422(client):
    client.post("/api/v1/auth/register", json=REG)
    assert client.post("/api/v1/auth/register", json=REG).status_code == 409
    r = client.post("/api/v1/auth/register", json={"email": "bad", "password": "short", "name": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_password_over_72_bytes_rejected(client):
    assert client.post("/api/v1/auth/register", json={**REG, "password": "x" * 80}).status_code == 422


def test_rbac(client, admin):
    client.post("/api/v1/auth/register", json=REG)
    h = login(client, "a@x.com", "Password123")
    assert client.get("/api/v1/admin/tests", headers=h).status_code == 403
    assert client.get("/api/v1/admin/tests").status_code == 401
    assert client.get("/api/v1/tests", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/api/v1/tests", headers=admin).status_code == 403


def test_lockout_after_failed_logins(client):
    client.post("/api/v1/auth/register", json=REG)
    for _ in range(5):
        assert client.post("/api/v1/auth/login", data={"username": "a@x.com", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", data={"username": "a@x.com", "password": "Password123"}).status_code == 423


def test_refresh_rotation_and_reuse_detection(client):
    client.post("/api/v1/auth/register", json=REG)
    client.post("/api/v1/auth/login", data={"username": "a@x.com", "password": "Password123"})
    old = client.cookies.get("refresh_token")
    assert client.post("/api/v1/auth/refresh").status_code == 200
    assert client.cookies.get("refresh_token") != old
    thief = new_client()
    thief.cookies.set("refresh_token", old, path="/api/v1/auth")
    assert thief.post("/api/v1/auth/refresh").status_code == 401
    assert client.post("/api/v1/auth/refresh").status_code == 401  # family revoked


def test_logout_revokes_refresh(client):
    client.post("/api/v1/auth/register", json=REG)
    client.post("/api/v1/auth/login", data={"username": "a@x.com", "password": "Password123"})
    tok = client.cookies.get("refresh_token")
    client.post("/api/v1/auth/logout")
    c2 = new_client()
    c2.cookies.set("refresh_token", tok, path="/api/v1/auth")
    assert c2.post("/api/v1/auth/refresh").status_code == 401


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-frame-options"] == "DENY" and "default-src" in r.headers["content-security-policy"]


def test_health_reports_server_time_for_the_system_check(client):
    from datetime import datetime, timezone
    r = client.get("/api/v1/health").json()
    t = datetime.fromisoformat(r["meta"]["server_time"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - t).total_seconds()) < 5 and r["data"]["status"] == "ok"


def test_seed_admin_is_idempotent_and_survives_a_boot_race(db_session, monkeypatch):
    """Four gunicorn workers booting together all see 'no admin' and all insert. The losers must not crash."""
    from app import main
    from app.models import Role, User
    main.seed_admin()                       # an admin already exists (created by the fixture): nothing happens
    main.seed_admin()
    with db_session() as s:
        assert s.query(User).filter(User.role == Role.admin.value).count() == 1
    # simulate the race: this worker's "does an admin exist?" check wrongly says no
    class Blind:
        def __init__(self, real): self.real = real
        def query(self, *a, **k):
            q = self.real.query(*a, **k)
            class Q:
                def filter(self, *f): return self
                def first(self): return None
            return Q()
        def __getattr__(self, n): return getattr(self.real, n)
        def __enter__(self): return self
        def __exit__(self, *a): return self.real.__exit__(*a)
    real = main.SessionLocal
    monkeypatch.setattr(main, "SessionLocal", lambda: Blind(real()))
    main.seed_admin()                       # tries to insert a duplicate -> must swallow the IntegrityError
    with db_session() as s:
        assert s.query(User).filter(User.role == Role.admin.value).count() == 1


def test_clean_page_urls_and_legacy_redirects(client):
    for path in ("/login", "/dashboard", "/help", "/admin", "/admin/help", "/admin/test-center", "/admin/test-center/3", "/admin/test-center/3/questions", "/admin/test-center/3/review",
                 "/admin/candidates", "/admin/colleges", "/test/7"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    for old, new in (("/static/login.html", "/login"), ("/static/dashboard.html", "/dashboard"), ("/static/admin.html", "/admin"),
                     ("/static/help.html", "/help"), ("/static/admin-help.html", "/admin/help"),
                     ("/static/test.html?test=4", "/test/4"), ("/static/testcenter.html?test=2", "/admin/test-center/2"),
                     ("/static/testcenter.html", "/admin/test-center")):
        r = client.get(old, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == new, old
    assert client.get("/admin/nope", headers={"accept": "text/html"}).status_code == 404
    assert client.get("/admin/test-center/3/bogus", headers={"accept": "text/html"}).status_code == 404
    assert client.get("/test/abc").status_code == 422 or client.get("/test/abc").status_code == 404
    assert client.get("/static/css/app.css").status_code == 200      # assets still served from /static
