import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.v1 import admin_candidates, admin_integrity, admin_staff, admin_tests, auth, candidate, colleges
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.ratelimit import limiter
from app.core.security import hash_password, utcnow
from app.db.session import SessionLocal
from app.models import Role, User
from app.services.attempts import sweep_expired

settings = get_settings()
log = logging.getLogger("app")
STATIC = Path(__file__).resolve().parent.parent / "static"

# Everything (Bootstrap, jQuery, Monaco) is served from /static/vendor, so no third-party hosts are allowed:
# labs without internet access work, and a compromised CDN cannot inject script.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")


def seed_admin() -> None:
    """Create the first admin. Safe when several workers boot at the same moment (gunicorn -w N): the unique
    email constraint decides the winner and the others simply carry on."""
    with SessionLocal() as db:
        if db.query(User).filter(User.role == Role.admin.value).first():
            return
        generated = not settings.admin_password
        pw = settings.admin_password or secrets.token_urlsafe(12)
        db.add(User(email=settings.admin_email.lower(), password_hash=hash_password(pw), role=Role.admin.value, full_name="Administrator"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # another worker created it first
            return
        if generated:  # only the worker that really created the account may announce a password
            log.warning("ADMIN_PASSWORD not set; generated admin password for %s: %s (shown once)", settings.admin_email, pw)


async def _sweeper():
    while True:
        await asyncio.sleep(settings.sweeper_interval_seconds)
        try:
            n = await asyncio.to_thread(_sweep_once)
            if n:
                log.info("sweeper finalized %d expired attempts", n)
        except Exception:
            log.exception("sweeper failed")


def _sweep_once() -> int:
    with SessionLocal() as db:
        return sweep_expired(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_admin()
    task = asyncio.create_task(_sweeper())
    yield
    task.cancel()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan,
              docs_url="/docs" if settings.environment != "prod" else None,
              redoc_url=None, openapi_url="/openapi.json" if settings.environment != "prod" else None)
app.state.limiter = limiter
install_error_handlers(app)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    if settings.cookie_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # always revalidate (ETag) so UI fixes ship instantly
    return resp


API = "/api/v1"
for r in (auth.router, admin_tests.router, admin_candidates.router, admin_integrity.router, admin_staff.router, colleges.admin, colleges.public, candidate.router):
    app.include_router(r, prefix=API)


@app.get(f"{API}/health", tags=["meta"])
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    # server_time lets the browser's pre-test system check detect a wrong computer clock
    return {"data": {"status": "ok"}, "error": None, "meta": {"server_time": utcnow().isoformat() + "Z"}}


# ---- clean page URLs (no "/static/x.html" in the address bar). Only assets live under /static.
ADMIN_TABS = {"tests", "colleges", "candidates", "integrity", "results", "audit"}
PAGES = {"/login": "login.html", "/forgot": "forgot.html", "/reset": "reset.html", "/dashboard": "dashboard.html", "/help": "help.html", "/admin": "admin.html",
         "/admin/help": "admin-help.html", "/admin/test-center": "testcenter.html"}


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})


for _path, _file in PAGES.items():
    app.add_api_route(_path, (lambda f=_file: _page(f)), methods=["GET"], include_in_schema=False)


@app.get("/test/{test_id}", include_in_schema=False)
def test_page(test_id: int):
    return _page("test.html")


@app.get("/admin/test-center/{test_id}", include_in_schema=False)
def test_center_page(test_id: int):
    return _page("testcenter.html")


@app.get("/admin/test-center/{test_id}/{step}", include_in_schema=False)
def test_center_step_page(test_id: int, step: str):
    if step not in {"settings", "questions", "review"}:
        raise HTTPException(404, "Not Found")
    return _page("testcenter.html")


@app.get("/admin/{tab}", include_in_schema=False)
def admin_tab_page(tab: str):
    if tab not in ADMIN_TABS:
        raise HTTPException(404, "Not Found")
    return _page("admin.html")


# old addresses keep working (bookmarks, emailed links) and move to the clean ones
LEGACY = {"login": "/login", "dashboard": "/dashboard", "help": "/help", "admin": "/admin", "admin-help": "/admin/help"}


@app.get("/static/{name}.html", include_in_schema=False)
def legacy_page(name: str, request: Request):
    q = request.query_params
    if name == "test" and q.get("test", "").isdigit():
        return RedirectResponse(f"/test/{q['test']}", status_code=301)
    if name == "testcenter":
        return RedirectResponse(f"/admin/test-center/{q['test']}" if q.get("test", "").isdigit() else "/admin/test-center", status_code=301)
    if name == "404":
        return FileResponse(STATIC / "404.html", status_code=404)
    if name in LEGACY:
        return RedirectResponse(LEGACY[name], status_code=301)
    raise HTTPException(404, "Not Found")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/login")
