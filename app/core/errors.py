import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app")
NOT_FOUND_PAGE = Path(__file__).resolve().parents[2] / "static" / "404.html"


def ok(data=None, meta: dict | None = None) -> dict:
    return {"data": data, "error": None, "meta": meta or {}}


def err_body(code: str, message: str, details=None) -> dict:
    return {"data": None, "error": {"code": code, "message": message, "details": details}, "meta": {}}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(err_body(e.code, e.message, e.details), status_code=e.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        # browsers asking for a missing page get a real 404 page; API clients keep the JSON envelope
        if e.status_code == 404 and not request.url.path.startswith("/api/") \
                and "text/html" in request.headers.get("accept", ""):
            return FileResponse(NOT_FOUND_PAGE, status_code=404)
        return JSONResponse(err_body(f"http_{e.status_code}", str(e.detail)), status_code=e.status_code,
                            headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        details = [{"loc": [str(x) for x in d["loc"]], "msg": d["msg"]} for d in e.errors()]
        return JSONResponse(err_body("validation_error", "Invalid request", details), status_code=422)

    @app.exception_handler(RateLimitExceeded)
    async def _rl(_: Request, e: RateLimitExceeded):
        return JSONResponse(err_body("rate_limited", "Too many requests, slow down"), status_code=429)

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse(err_body("internal_error", "Internal server error"), status_code=500)
