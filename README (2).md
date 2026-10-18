# Campus Recruitment Assessment Platform

> A secure, proctored, multi-stage online assessment system for campus recruitment:
> **Aptitude Test → Behavioural Test → Coding Test**, built with **FastAPI (REST)** + **Bootstrap 5 + jQuery**.

---

## 1. Project Overview

The platform lets a college/company admin create and manage three sequential assessment
stages. A candidate must clear the cutoff of each stage to unlock the next. The test
window is proctored in-browser: fullscreen enforcement, tab-switch/window-blur
detection, copy–paste blocking, and an auto-submitting server-side timer.

### Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Database | PostgreSQL (production) / SQLite (dev) |
| ORM & Migrations | SQLAlchemy 2.x + Alembic |
| Auth | OAuth2 Password Flow + JWT (access + refresh tokens), bcrypt/passlib |
| Validation | Pydantic v2 |
| Frontend | Bootstrap 5, jQuery 3.x, vanilla JS proctoring layer |
| Code Execution | Judge0 CE (self-hosted Docker) — sandboxed code runner |
| Reverse proxy / deploy | Nginx + HTTPS, Docker Compose |

---

## 2. User Roles & Functional Requirements

### 2.1 Candidate
- Register / log in (email + password, verified email optional for MVP).
- View dashboard of available tests assigned to them.
- Take tests in strict sequence: Aptitude → Behaviour → Coding.
- **Cannot start next stage unless previous stage score ≥ admin-set cutoff.**
- Enforced fullscreen during the test; violations logged and shown as warnings.
- Server-side countdown; auto-submit on timeout.
- Save answers incrementally (autosave every N seconds + on navigation).
- View own result/scorecard after completion (if admin enables it).

### 2.2 Admin
- Create / edit / delete tests: title, stage (aptitude/behaviour/coding), duration,
  start/end window, cutoff score, question set.
- Manage question bank per stage:
  - *Aptitude:* MCQ with 4 options, single correct answer, marks, difficulty.
  - *Behaviour:* Likert-scale / scenario MCQ statements.
  - *Coding:* problem statement, input/output format, sample test cases,
    hidden test cases, time/memory limits, function signature.
- Import questions in bulk (CSV/JSON).
- Register candidates individually or bulk upload (CSV); assign tests to batches.
- View list of all candidates, their status (not started / in progress /
  completed / disqualified), and detailed results:
  - Score per stage, per-question breakdown, coding submission history.
  - **Proctoring log**: tab switches, fullscreen exits, copy attempts, timestamps.
- Shortlist / export results (CSV), filter by cutoff.
- One admin account seeded by default; role-based access control (RBAC).

---

## 3. Assessment Flow (State Machine)

```
REGISTERED ──start──> APTITUDE_IN_PROGRESS ──submit──> APTITUDE_EVALUATED
     │ pass cutoff                                        │ fail cutoff
     ▼                                                    ▼
BEHAVIOUR_IN_PROGRESS ◀──────────────          CANDIDATE_REJECTED (end)
     │ submit
     ▼
BEHAVIOUR_EVALUATED
     │ pass cutoff
     ▼
CODING_IN_PROGRESS ──submit──> CODING_EVALUATED ──> FINAL_RESULT (eligible)
```

- All transitions happen **server-side**; the client never decides pass/fail.

---

## 4. Security & Anti-Cheating Requirements (Proctoring Layer)

> Rule of thumb from industry practice: no single control makes an exam secure —
> security comes from **layers** so each control raises the cost of one malpractice
> method (fullscreen, tab-blocking, copy-paste lock, shuffle, auto-submit, audit trail).

### 4.1 Client-side controls (JS — detection + deterrence)
| Control | Mechanism |
|---|---|
| Fullscreen lock | `element.requestFullscreen()` on start; listen to `fullscreenchange`; if exited → warning + log event + force re-entry (max N warnings → auto-submit) |
| Tab / window switch | `visibilitychange`, `window.onblur`, `document.hasFocus()` → log event + warning |
| Copy / paste / cut | block `copy`, `paste`, `cut`, `contextmenu` events |
| DevTools & shortcuts | block F12, Ctrl+Shift+I/J/C, Ctrl+U, Ctrl+P, Ctrl+S |
| Text selection / drag | `user-select: none`, block `dragstart` |
| Resize / minimize | `window.onresize` detect → log event |

**Important limitation to document:** JavaScript cannot truly "lock" a computer
(a real lockdown requires a native app like Respondus). So the design is:
- **College-lab setting (recommended):** conduct in a supervised computer lab —
  this is the setting where browser-level controls are considered sufficient by
  academic IT guidance.
- Violations are **logged and surfaced to the admin** rather than pretending to
  block everything; repeated violations can trigger auto-submit/disqualify.
- Admin sees the full violation log when reviewing results (audit-friendly).

### 4.2 Server-side controls
- **Server-authoritative timer:** start timestamp + duration stored in DB;
  every answer submission validated against server time (client clock ignored).
- **One active attempt per candidate per test** (unique constraint, DB transaction).
- **Questions delivered one-at-a-time or paginated**; full paper never sent to
  browser at once (prevents scraping entire paper).
- **Question & option shuffling:** per-candidate random order seed stored server-side.
- **Auth hardening:** bcrypt hashing, short-lived JWT access tokens (15 min) +
  refresh tokens (stored hashed in DB, rotation on use), logout blacklist.
- **RBAC:** every endpoint checks role (`admin` vs `candidate`) via dependency
  injection; candidates can only access their own attempt IDs (ownership check).
- **Rate limiting** on login and submission endpoints (slowapi).
- **Input validation** on every endpoint via Pydantic schemas.
- **Idempotency:** submit endpoints accept an idempotency key so retries /
  double-clicks don't create duplicate submissions.
- **Audit logging:** every login, test start, submit, violation event written to
  an audit table.
- **HTTPS only**, secure cookies, CORS restricted to the app origin,
  security headers (HSTS, X-Frame-Options, CSP).

### 4.3 Coding test integrity
- Code executed in **sandboxed Judge0** (isolated containers, no network, CPU/
  memory limits) — never via `exec()` on the app server.
- Plagiarism check: store code submissions; optional Moss-style similarity check.

---

## 5. REST API Architecture

### Conventions
- Base URL: `/api/v1`
- JSON everywhere; consistent envelope:
  `{ "data": ..., "error": null, "meta": {...} }`
- HTTP status codes used properly (200/201/400/401/403/404/409/422/429).
- Pagination (`?page=&page_size=`) and filtering on list endpoints.
- Versioned from day one.

### Endpoint Map (summary)

**Auth**
| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Candidate registration |
| POST | `/auth/login` | OAuth2 password → tokens |
| POST | `/auth/refresh` | Refresh token rotation |
| POST | `/auth/logout` | Invalidate refresh token |

**Admin — Tests**
| Method | Path | Description |
|---|---|---|
| POST | `/admin/tests` | Create test (stage, window, cutoff, duration) |
| GET | `/admin/tests` | List tests |
| PUT | `/admin/tests/{id}` | Update test |
| DELETE | `/admin/tests/{id}` | Delete test |
| POST | `/admin/tests/{id}/questions` | Add question |
| POST | `/admin/tests/{id}/questions/import` | Bulk import CSV/JSON |

**Admin — Candidates & Results**
| Method | Path | Description |
|---|---|---|
| POST | `/admin/candidates` | Create candidate / bulk upload |
| GET | `/admin/candidates` | List candidates (status filter, pagination) |
| GET | `/admin/candidates/{id}/results` | Full result + proctoring log |
| GET | `/admin/results/export?test_id=` | CSV export |
| POST | `/admin/candidates/{id}/disqualify` | Manual disqualify |

**Candidate — Test Taking**
| Method | Path | Description |
|---|---|---|
| GET | `/tests` | My assigned tests + current stage status |
| POST | `/tests/{id}/start` | Begin stage → returns question 1 + attempt token |
| GET | `/attempts/{id}/question?n=` | Fetch current question (server-shuffled order) |
| PUT | `/attempts/{id}/answers/{qid}` | Save answer (autosave, idempotent) |
| POST | `/attempts/{id}/violations` | Report proctoring event (tab switch, fullscreen exit…) |
| POST | `/attempts/{id}/submit` | Final submit → server evaluation |
| POST | `/attempts/{id}/code/run` | Run code against sample cases (Judge0) |
| POST | `/attempts/{id}/code/submit` | Final code submission → judge against hidden cases |
| GET | `/attempts/{id}/result` | My scorecard (if enabled) |

### Key Backend Optimizations
- Async SQLAlchemy + connection pooling; Pydantic v2 for fast serialization.
- Background tasks (FastAPI `BackgroundTasks` or Celery + Redis for scale) for
  evaluation, result computation, and exports.
- Eager-loading (`selectinload`) to avoid N+1 queries on results.
- Indexes on: `attempt(candidate_id, test_id)`, `violation(attempt_id)`,
  `question(test_id)`, foreign keys.
- Judge0 calls batched/queued; per-test-case time limits enforced by sandbox.

### Frontend API Consumption Optimizations
- Single `api.js` wrapper (jQuery `$.ajax`) with: JWT auto-attach, 401 →
  silent refresh → retry queue, global error toast handler.
- **Debounce/dirty-check** autosave: only send changed answers.
- Prefetch next question while user answers current one.
- Debounce violation events locally, batch-send every 5 s (fire-and-forget
  `navigator.sendBeacon` on unload for reliability).
- HTML templating via lightweight JS template strings; Bootstrap for layout —
  no heavy SPA framework needed for this scope.

---

## 6. Database Schema (logical)

```
users(id, email, password_hash, role[candidate|admin], created_at)
tests(id, title, stage[aptitude|behaviour|coding], duration_min,
      start_at, end_at, cutoff_score, is_active)
questions(id, test_id, type[mcq|likert|coding], body, options_json,
          correct_option, marks, difficulty, order_seed)
candidates(id, user_id, name, college, branch, batch)
candidate_tests(id, candidate_id, test_id, status)
attempts(id, candidate_test_id, stage, started_at, server_deadline,
         submitted_at, score, status[active|submitted|expired|disqualified])
answers(id, attempt_id, question_id, selected_option, is_correct,
        UNIQUE(attempt_id, question_id))
code_submissions(id, attempt_id, question_id, language, source_code,
          status, runtime_ms, memory_kb, passed_cases, total_cases)
violations(id, attempt_id, type[tab_switch|fullscreen_exit|copy_paste|
          resize|blur], occurred_at, client_meta)
refresh_tokens(id, user_id, token_hash, expires_at, revoked)
audit_log(id, actor_id, action, entity, entity_id, at, ip)
```

---

## 7. Non-Functional Requirements
- **Performance:** p95 API latency < 300 ms (list endpoints), support ~200
  concurrent candidates in a college lab (vertical scaling + pooling enough).
- **Availability:** stateless API (JWT) → can run multiple Uvicorn workers.
- **Backups:** scheduled PostgreSQL dumps.
- **Privacy:** proctoring metadata retained per policy, HTTPS everywhere.
- **Auditability:** every score traceable to answers, violations, IP, timestamps.

---

## 8. Step-by-Step Implementation Plan

> Build and verify in small vertical slices. Do **not** build all three test
> types before wiring one end-to-end.

### Phase 0 — Project skeleton (Day 1)
1. `git init`; repo layout:
   ```
   app/
     main.py  routers/  models/  schemas/  core/  services/
     api/v1/  db/  workers/  tests/
   static/  templates/  docker-compose.yml  alembic/
   ```
2. `pip install fastapi uvicorn[standard] sqlalchemy alembic pydantic-settings
   python-jose passlib[bcrypt] python-multipart slowapi psycopg2-binary httpx
   redis celery` (dev: `pytest`, `httpx`).
3. Settings via Pydantic Settings (`.env`: DB URL, JWT secret, Judge0 URL).
4. Docker Compose: `api` (uvicorn --reload), `db` (Postgres), `judge0`
   (self-hosted CE), optional `redis`.
5. `/health` endpoint; run first migration with `users` table; confirm up.

### Phase 1 — Auth + RBAC (Days 2–3)
6. `POST /auth/register`, `POST /auth/login` (OAuth2 form → JWT pair),
   `/auth/refresh` with rotation + revocation table.
7. `get_current_user` + `require_admin` FastAPI dependencies.
8. Rate-limit login (slowapi); seed default admin.
9. Minimal Bootstrap pages: login/register; store token in
   `sessionStorage`; wire the `api.js` wrapper with refresh-retry logic.

### Phase 2 — Admin: tests & questions CRUD (Days 4–6)
10. Models: `tests`, `questions`; Alembic migration; Pydantic schemas.
11. Admin endpoints: create test, add/edit/delete question, CSV import,
    list tests/questions.
12. Admin UI (Bootstrap + jQuery DataTables): tests list, test builder page
    (dynamic MCQ option rows via jQuery), CSV upload, candidate list.

### Phase 3 — Candidate flow: take an MCQ test (Days 7–10) — *first full slice*
13. Candidate dashboard; assign test to candidates (admin UI).
14. `POST /tests/{id}/start` → creates `attempt`, records `server_deadline`,
    returns first question only.
15. Question pagination endpoint with server-side shuffle seed.
16. Autosave `PUT answers` (idempotent upsert); `POST submit` → score MCQ in
    a background task → update status.
17. Client timer synced to server deadline; **auto-submit at 0** via
    `submit()` call.
18. **Proctoring JS module** (`static/js/proctor.js`): fullscreen request,
    violation listeners, batch send to `/violations`. Test it manually.
19. Cutoff gate: `GET /tests` hides next stage unless previous passed.

### Phase 4 — Coding test with Judge0 (Days 11–13)
20. Question type `coding` + problem/cases model.
21. `POST code/run` → forward to Judge0 (only sample cases);
    `POST code/submit` → hidden cases, store verdict.
22. Candidate coding UI: simple textarea editor (or embed Monaco via CDN),
    language select, run output panel.
23. Score coding stage; wire into the stage state machine.

### Phase 5 — Behavioural test + results & proctoring review (Days 14–15)
24. Likert-type question rendering (reuse MCQ engine — same table, different
    `type`; scoring = weighted sum, no "correct" answer).
25. Admin result pages: per-candidate scorecard, per-question breakdown,
    **violation timeline**, CSV export, shortlist filter.

### Phase 6 — Security hardening & performance (Days 16–17)
26. Security headers, CORS lockdown, HTTPS via Nginx (compose), cookie
    settings for refresh token.
27. N+1 audit on list endpoints; add `selectinload`; add DB indexes.
28. Load test with `locust` (login + fetch question + submit).
29. Backups, audit log review, admin action logging.

### Phase 7 — Polish & deploy (Days 18–20)
30. Run a **mock/practice test round** with a few real users — this is a
    documented best practice before high-stakes use (catches webcam/
    fullscreen/browser issues early).
31. Error pages, toasts, loading states, mobile-friendly layouts.
32. Production compose on a VPS: Nginx TLS, Postgres volumes, Judge0
    isolation, gunicorn/uvicorn workers, `alembic upgrade head` on boot.
33. User/admin documentation; go live for campus drive.

### Definition of Done (per slice)
- API documented (OpenAPI auto-docs at `/docs`); happy + error paths tested.
- Ownership/RBAC checks tested (candidate A cannot read attempt of B).
- UI calls API only through `api.js`; no business logic in page scripts.
- Migration + seed data committed; Docker Compose boots the whole stack.

---

## 9. Known Limitations & Honest Security Notes
- Browser-based proctoring **detects and deters**; it cannot fully prevent a
  second device/phone or a second monitor. For high-stakes hiring, combine with
  a supervised lab setting or live/human proctoring.
- Fullscreen exit cannot be truly blocked in a normal browser — the design logs
  it, warns the candidate, and (per admin policy) auto-submits after N offenses.
- Client-side violation reports can be tampered with by a technical user —
  they are treated as **signals**, and server-side signals (timing anomalies,
  answer patterns) complement them.

---

*Generated as a requirements & implementation-plan document. No application
code is included per project scope — start from Phase 0.*
