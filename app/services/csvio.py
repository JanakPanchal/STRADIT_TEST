import csv
import io
import json

from app.core.errors import ApiError

MAX_BYTES = 2_000_000
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v) -> str:
    """Neutralise spreadsheet formula injection (a candidate named '=HYPERLINK(...)')."""
    s = "" if v is None else str(v)
    return "'" + s if s.startswith(_DANGEROUS) else s


def read_upload(raw: bytes) -> str:
    if len(raw) > MAX_BYTES:
        raise ApiError(413, "file_too_large", "File too large (max 2 MB)")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(400, "bad_encoding", "File must be UTF-8")


def parse_csv(text: str) -> list[dict]:
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text))]


def parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ApiError(400, "bad_json", f"Invalid JSON: {e}")


def to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([safe_cell(c) for c in r])
    return buf.getvalue()
