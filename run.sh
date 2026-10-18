#!/usr/bin/env bash
# Local runner:  ./run.sh            -> start server on http://localhost:8000
#                ./run.sh test       -> run the test suite
#                PORT=9000 ./run.sh  -> custom port
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo ">> Creating virtualenv"
  python3 -m venv .venv
fi
source .venv/bin/activate

if [ ! -f .venv/.deps_installed ] || [ requirements.txt -nt .venv/.deps_installed ]; then
  echo ">> Installing dependencies"
  pip install -q -r requirements.txt
  touch .venv/.deps_installed
fi

[ -f .env ] || { echo ">> Creating .env from .env.example"; cp .env.example .env; }
# stable dev secret so --reload restarts don't log everyone out
grep -q '^JWT_SECRET=' .env || echo "JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" >> .env

if [ "${1:-}" = "test" ]; then
  exec python -m pytest tests -q
fi

echo ">> Applying database migrations"
alembic upgrade head

PORT="${PORT:-8000}"
echo ">> App:   http://localhost:${PORT}"
echo ">> Docs:  http://localhost:${PORT}/docs"
echo ">> Admin: see ADMIN_EMAIL / ADMIN_PASSWORD in .env"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
