# README Review — Corrections & Enhancements

Verdict: the README is a **sound high-level design** (layered anti-cheat, server-authoritative
timer, server-side state machine, sandboxed code execution). It has **dependency, schema and
internal-consistency problems** that would have caused bugs if followed literally. Each item
below states what the README says, why it is wrong or risky, and what the implementation does.

## A. Incorrect / risky technology choices

| # | README | Problem | Implementation |
|---|---|---|---|
| A1 | `python-jose` for JWT | Poorly maintained; had algorithm-confusion and JWE-bomb CVEs (CVE-2024-33663/33664). FastAPI's own docs moved off it. | **PyJWT**, algorithm pinned to HS256 on decode. |
| A2 | `passlib[bcrypt]` | Last release 1.7.4 (2020); breaks with `bcrypt>=4.1` and bcrypt 5.x (which also raises on passwords >72 bytes). | **`bcrypt` used directly**; passwords capped at 72 bytes by validation. |
| A3 | "Async SQLAlchemy" + `psycopg2-binary` | Contradiction: async needs `asyncpg`/`aiosqlite`, and Alembic then needs an async env. | **Sync SQLAlchemy 2.x** in FastAPI's threadpool. Ample for ~200 candidates, far simpler, fully testable. |
| A4 | Judge0 "no network, isolated" | True only with correct config: Judge0 needs `privileged` containers and (v1.13) cgroup v1 hosts. Easy to misdeploy. | Judge0 client is configurable/pluggable; `docker-compose.yml` documents the privileged + cgroup requirement. |

## B. Security design gaps

| # | Gap | Fix |
|---|---|---|
| B1 | **Per-IP login rate limit would lock out an entire lab** — 200 candidates share one NAT IP. | Generous per-IP limit + **per-account** lockout after repeated failures. |
| B2 | slowapi in-memory storage is per-worker; behind Nginx every request shows the proxy IP. | Storage URI configurable (Redis in prod); proxy-header handling documented. |
| B3 | Token storage contradictory (sessionStorage in Phase 1, cookies in 4.2/Phase 6). | Access token in `sessionStorage`; **refresh token in HttpOnly, SameSite=Strict cookie** scoped to `/api/v1/auth`. |
| B4 | "Logout blacklist" for access tokens is redundant with 15-min tokens and adds state. | Revoke refresh token family on logout; access user re-loaded from DB each request so disabled/disqualified users are cut off immediately. |
| B5 | No refresh-token **reuse detection**. | Token *families*: reuse of a rotated token revokes the whole family. |
| B6 | Seeded admin with default password is a classic hole. | Admin created from env; if unset, random password generated and logged once. |
| B7 | CSV export is vulnerable to **formula injection** (names like `=cmd(...)`). | Cells starting with `= + - @` are neutralised. |
| B8 | Bulk-created candidates have no password story. | Generated one-time passwords returned in the bulk response; `must_change_password` flag. |
| B9 | Client-supplied violation timestamps are untrusted. | Server time is authoritative; client time stored only as metadata. |

## C. Schema bugs / omissions

| # | Gap | Fix |
|---|---|---|
| C1 | `questions.order_seed` — shuffle seed is **per candidate**, not per question. | `attempts.shuffle_seed`; question *and* option order derived deterministically from it. |
| C2 | Hidden/sample **test cases have no table**. | `test_cases(question_id, input, expected_output, is_sample, weight)`. |
| C3 | Likert scoring "weighted sum" but only `correct_option` exists. | Options are `{text, score}`; MCQ uses `correct_option`, Likert sums option scores. |
| C4 | State machine states exist in the diagram but not in the schema. | `candidates.state` enum incl. `DISQUALIFIED`. |
| C5 | "One active attempt per candidate per test" ambiguous. | `UNIQUE(candidate_test_id)` on attempts (no silent retakes); resume returns the same attempt. |
| C6 | Missing: `max_violations`, `show_result`, `violation_count`, `must_change_password`, lockout fields, refresh `family_id`. | Added. |
| C7 | "Cutoff" unit undefined. | Absolute marks, compared with `>=` (README rule). |
| C8 | Shuffled options must not leak original index. | Client sees only shuffled position; server maps back. |

## D. Logic / consistency issues

| # | Issue | Resolution |
|---|---|---|
| D1 | "Auto-submit server-side timer" — a server can't push to a browser. | **Lazy expiry** on every request + background **sweeper** task; both idempotent. |
| D2 | State-machine diagram is garbled (behaviour/coding fail paths missing). | Rewritten: failing any stage ⇒ `CANDIDATE_REJECTED`; passing coding ⇒ `FINAL_RESULT`. |
| D3 | Window vs duration: late starters could outlast `end_at`. | `deadline = min(start + duration, end_at)`. |
| D4 | `blur` + `visibilitychange` + `fullscreenchange` fire together → one action counted 3×, causing unfair auto-submits. | Client debounce **and** server clustering (counted violations within 2 s collapse into one). `resize` is logged but never counted. |
| D5 | Idempotency key table is over-engineering for `submit`. | Submit and answer-save are **state-idempotent** (second submit returns the first result). |
| D6 | Blocking copy/paste would break typing in the code editor. | Paste/copy blocked on the test page; right-click/shortcuts blocked everywhere; documented. |
| D7 | Phase 7 mentions **webcam** though nothing else does. | Out of scope; removed. |
| D8 | Fullscreen API unsupported on iPhone Safari; Esc can't be intercepted. | Documented: desktop browsers only; Esc handled via detect-and-re-enter. |
| D9 | Coding "function signature" vs stdin/stdout judging. | Judging is stdin/stdout; `function_signature` is a starter-code hint shown to the candidate. |
| D10 | MCQ scoring "in a background task" races with the result page. | Scoring is O(questions) and done **inside the submit transaction**; only Judge0 work is slow and happens at `code/submit`. |

## E. Implementation status (kept in sync with the code)

Every item above is implemented. Where the implementation differs from the original README, this table says so.

| Item | Status | Where / note |
|---|---|---|
| A1 PyJWT, A2 `bcrypt` directly, A3 sync SQLAlchemy | Done | `app/core/security.py`, `app/db/session.py` |
| A4 Judge0 deployment caveats | Documented, **not run** | `docker-compose.yml`; Judge0 itself has not been exercised against a real instance |
| B1 lockout instead of strict per-IP limit | Done | `auth.py`; nginx edge limit is deliberately generous |
| B2 shared rate-limit storage / proxy headers | Done | `RATE_LIMIT_STORAGE` (Redis in compose), gunicorn `--forwarded-allow-ips` |
| B3 token storage, B4, B5 refresh families | Done | HttpOnly cookie, rotation, reuse detection (tested) |
| B6 admin from env, B7 CSV injection, B8 one-time passwords, B9 server time | Done | tested |
| C1-C8 schema fixes | Done | plus later migrations 0002-0004 (question pool, colleges, code drafts) |
| D1 lazy expiry + sweeper, D3 window cap, D4 clustering, D5 state-idempotent submit, D10 in-transaction scoring | Done | `app/services/attempts.py`, tested |
| D6 copy/paste policy | **Revised** | Blocked page-wide; inside the code editor, copying *within* the editor is allowed, anything pasted from outside is blocked and logged (enforced at window capture, verified in a browser) |
| D8 fullscreen/Esc limits | Documented to candidates | rules page + help guide |
| D9 coding judged on stdin/stdout | Done | `function_signature` is a starter template |
| **Plagiarism / similarity check (README 4.3)** | **Done (was deferred)** | `app/services/integrity.py`: renamed-variable-proof code similarity, shared-wrong-answer detection, fast-score flags; admin *Integrity* tab. Signals for human review, not verdicts |
| **Server-side signals (README 9)** | **Done** | same module |
| PostgreSQL | **Verified** (embedded Postgres, no Docker) | All migrations incl. the colleges data migration and a downgrade/upgrade round trip; the full test suite (87) passes on both SQLite and PostgreSQL (`TEST_DATABASE_URL`) |
| Locust load test | **Run on PostgreSQL, 4 gunicorn workers, 200 candidates** | 8,601 requests, 0.05% failures, no server errors. Answer save p95 52 ms, question fetch p95 77 ms (< 300 ms target met). **Login p50 3.6 s in a simultaneous burst** (bcrypt cost 12, CPU-bound, load generator on the same machine); `BCRYPT_ROUNDS` and staggering are the mitigations. SQLite is dev-only: it failed this test (writer serialisation, file corruption) |
| Multi-worker boot race | **Found and fixed by the Postgres run** | Four workers starting together each tried to create the admin; the losers crashed on the unique email. `seed_admin` is now race-safe (tested) |
| Moss (external service) | Not used | in-house similarity instead |
| DataTables | Replaced | server-paginated Bootstrap tables |
| Email verification (optional in README) | Not implemented | not required for MVP |
| Celery/Redis workers | Not needed | scoring is O(questions) inside the submit transaction; the sweeper is an in-process task |

| **Analytics & item analysis** | Done | `app/services/analytics.py`, admin *Results* tab: outcome, score distribution, timing, per-question item analysis (too easy / very hard / suspicious key / no spread / skipped), college and batch breakdown; pool-aware (judged only on candidates served the question); tested against hand-computed numbers |

| **Password reset & invitations** | Done | `/forgot`, `/reset`, `POST /auth/forgot|reset`, `POST /admin/candidates/invite`; single-use hashed expiring tokens, no user enumeration, 1 mail/min/account, reset signs out all sessions and unlocks; sign-in tracking + filter; console/SMTP/memory mail backends (SMTP tested against a fake server, **not a live provider**); HTML-escaped templates |

### Added beyond the original README (requested during the build)
Colleges (onboarding, registration dropdown, filters), Test Center with per-type builders and uploads with preview, question pool (random N per candidate), candidate/test edit and delete (with retake, unassign, reinstate), candidate filters/outcomes/export, coding playground (Monaco editor vendored locally, drafts, custom input, submission history), pre-test system check, rules + disqualification page, in-app candidate and admin guides.

## F. Known limits (be honest with stakeholders)
- Browser proctoring detects and deters; it cannot stop a second device (README section 9 still applies).
- Judge0 and PostgreSQL deployments are configured but have not been run end to end in this environment.
- Disqualification is a human decision; reaching the warning limit auto-submits and flags only.
