# Stradit Campus Assessment Platform

Secure, proctored, three-stage assessment (Aptitude → Behaviour → Coding). FastAPI + SQLAlchemy +
Bootstrap/jQuery. Original spec: `README (2).md`; review and deviations: `REVIEW.md`.

## Run locally
```bash
./run.sh          # http://localhost:8000   (creates .venv, .env, migrates, serves with reload)
./run.sh test     # test suite
```
Admin and demo-candidate credentials are in `.env` (`ADMIN_EMAIL`/`ADMIN_PASSWORD`, `DEMO_CANDIDATE_*`).
API docs: `/docs` (dev only).

## In-app help
- Candidates: **Help & guide** on the dashboard and rules page (`/help`): stages, rules, the coding playground,
  troubleshooting. The rules page also runs a **system check** (fullscreen, storage, screen size, connection, clock).
- Admins: **Overview** checklist and **Help & guide** (`/admin/help`).

## Production (Docker Compose)
```bash
cp .env.example .env     # set JWT_SECRET, ADMIN_PASSWORD, POSTGRES_PASSWORD, JUDGE0_AUTH_TOKEN, PUBLIC_ORIGIN
# edit deploy/judge0.conf passwords/token (AUTHN_TOKEN must equal JUDGE0_AUTH_TOKEN)
mkdir -p deploy/certs    # fullchain.pem + privkey.pem
docker compose up -d --build
```
`api` runs `alembic upgrade head` on boot, then gunicorn with `WEB_CONCURRENCY` workers.
Judge0 needs `privileged` containers and a cgroup-v1 host (see comments in `docker-compose.yml`).

## Email (invitations & password reset)
`MAIL_BACKEND=console` (default) prints emails to the server log; set `MAIL_BACKEND=smtp` plus `SMTP_*`, `MAIL_FROM` and
**`PUBLIC_URL`** (the address candidates open; every emailed link starts with it) for real delivery. Admins invite candidates from the
Candidates tab; candidates reset their own password at `/forgot`. Links are single-use, stored hashed, and expire (reset 30 min,
invitation 7 days). Tested against a real SMTP exchange; not yet tried against a live provider.

## Verified performance (PostgreSQL, 4 gunicorn workers, 200 simulated candidates, one laptop)
- 8,601 requests, 0.05% failures; no server-side errors. Saving an answer: p50 5 ms / p95 52 ms. Fetching a question: p95 77 ms.
- **Login is the slow step in a burst**: p50 3.6 s when all 200 sign in within seconds, because bcrypt (cost 12) is CPU-bound and the
  load generator and database shared the same machine. Mitigations: stagger sign-in, add workers/cores, or lower `BCRYPT_ROUNDS` to 10.
- Connection pool: `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` are **per worker**; keep `workers x (size + overflow)` below PostgreSQL
  `max_connections` (default 100). Defaults 15 + 10 suit 4 workers.
- Reproduce without Docker: `pip install -r requirements-dev.txt`, `python scripts/pg_dev.py <dir>`, set `DATABASE_URL`
  to the printed URL, `alembic upgrade head`, then `gunicorn ... -w 4` and `locust` (see Operations).

## Operations
- Backups: `scripts/backup.sh` (cron it; 14-day retention; restore command inside the script).
- Load test: `python scripts/prepare_loadtest.py <url> <admin> <pw> 200 20 && locust -f locustfile.py --host <url> -u 200 -r 20`
- Audit trail: admin UI → *Audit log*, or `GET /api/v1/admin/audit`.

## Before a real drive
Run a practice round with a few real users on the real lab machines (fullscreen/browser quirks), set
`CORS_ORIGINS`/`PUBLIC_ORIGIN`, and review `REVIEW.md` §E for what is intentionally not implemented.
