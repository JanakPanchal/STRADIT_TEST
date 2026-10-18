"""Judge0 CE client. Code NEVER runs on the app server — only inside Judge0's isolate sandbox."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

# Judge0 CE default language ids (override if your instance differs: GET /languages)
LANGUAGE_IDS = {"python": 71, "java": 62, "cpp": 54, "c": 50, "javascript": 63}
ACCEPTED = 3


class JudgeUnavailable(Exception):
    pass


@dataclass
class CaseResult:
    passed: bool
    status: str
    time_ms: int | None = None
    memory_kb: int | None = None
    stdout: str = ""
    stderr: str = ""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _unb64(s: str | None) -> str:
    return base64.b64decode(s).decode(errors="replace") if s else ""


class Judge0Client:
    def __init__(self):
        self.s = get_settings()

    def _one(self, language: str, source: str, stdin: str, expected: str | None, tl_ms: int, mem_kb: int) -> CaseResult:
        headers = {"X-Auth-Token": self.s.judge0_auth_token} if self.s.judge0_auth_token else {}
        body = {"language_id": LANGUAGE_IDS[language], "source_code": _b64(source), "stdin": _b64(stdin),
                "cpu_time_limit": tl_ms / 1000, "wall_time_limit": tl_ms / 1000 * 3, "memory_limit": mem_kb}
        if expected is not None:  # custom runs have no expected output: Judge0 then reports Accepted if it ran cleanly
            body["expected_output"] = _b64(expected)
        try:
            r = httpx.post(f"{self.s.judge0_url}/submissions", params={"base64_encoded": "true", "wait": "true"},
                           json=body, headers=headers, timeout=self.s.judge0_timeout_s)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise JudgeUnavailable(str(e)) from e
        j = r.json()
        st = j.get("status", {})
        return CaseResult(
            passed=st.get("id") == ACCEPTED, status=st.get("description", "Unknown"),
            time_ms=int(float(j["time"]) * 1000) if j.get("time") else None, memory_kb=j.get("memory"),
            stdout=_unb64(j.get("stdout")),
            stderr=_unb64(j.get("stderr")) or _unb64(j.get("compile_output")))

    def judge(self, language: str, source: str, cases: list[tuple[str, str | None]], tl_ms: int,
              mem_kb: int) -> list[CaseResult]:
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(lambda c: self._one(language, source, c[0], c[1], tl_ms, mem_kb), cases))


def get_judge() -> Judge0Client:  # FastAPI dependency (overridden in tests)
    return Judge0Client()
