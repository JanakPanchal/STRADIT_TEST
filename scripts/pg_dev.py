"""Start a throw-away embedded PostgreSQL (no Docker) and print its SQLAlchemy URL. Dev/verification only.
   python scripts/pg_dev.py <data-dir>     (Ctrl+C stops it; the data directory is kept)"""
import sys
import time

import pgserver

srv = pgserver.get_server(sys.argv[1], cleanup_mode=None)
srv.psql("CREATE DATABASE assess;") if "assess" not in srv.psql("SELECT datname FROM pg_database;") else None
print(srv.get_uri(database="assess").replace("postgresql://", "postgresql+psycopg2://", 1), flush=True)
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
