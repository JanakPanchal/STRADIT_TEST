"""Creates N candidates + one aptitude test (M questions) via the admin API and writes
loadtest_users.csv for locustfile.py.   Usage:
  python scripts/prepare_loadtest.py http://localhost:8000 admin@example.com '<admin password>' 200 20
Run against a throwaway/staging instance, never production data."""
import csv
import sys

import httpx

base, email, password, n, m = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
api = base + "/api/v1"
c = httpx.Client(timeout=30)
tok = c.post(f"{api}/auth/login", data={"username": email, "password": password}).json()["data"]["access_token"]
h = {"Authorization": f"Bearer {tok}"}
tid = c.post(f"{api}/admin/tests", headers=h, json={"title": "Load test", "stage": "aptitude", "duration_min": 60,
             "cutoff_score": 1, "max_violations": 50}).json()["data"]["id"]
for i in range(m):
    c.post(f"{api}/admin/tests/{tid}/questions", headers=h, json={"type": "mcq", "body": f"Question {i + 1}",
           "options": [{"text": t} for t in "ABCD"], "correct_option": i % 4})
rows = []
for i in range(n):
    r = c.post(f"{api}/admin/candidates", headers=h, json={"email": f"load{i}@example.com", "name": f"Load {i}",
               "batch": "load"}).json()["data"]
    rows.append((r["email"], r["password"]))
c.post(f"{api}/admin/candidates/assign", headers=h, json={"test_id": tid, "batch": "load"})
with open("loadtest_users.csv", "w", newline="") as f:
    csv.writer(f).writerows([("email", "password", "test_id"), *[(e, p, tid) for e, p in rows]])
print(f"test {tid}, {n} users written to loadtest_users.csv")
