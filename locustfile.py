"""Load test: login -> start -> fetch questions -> autosave answers -> report violation -> submit.
Run:  locust -f locustfile.py --host http://localhost:8000 -u 200 -r 20
Target (README §7): p95 < 300 ms for ~200 concurrent candidates."""
import csv
import itertools
import random

from locust import HttpUser, between, task

USERS = list(csv.DictReader(open("loadtest_users.csv")))
_next = itertools.count()


class Candidate(HttpUser):
    wait_time = between(1, 4)

    def on_start(self):
        u = USERS[next(_next) % len(USERS)]
        r = self.client.post("/api/v1/auth/login", data={"username": u["email"], "password": u["password"]})
        self.h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
        d = self.client.post(f"/api/v1/tests/{u['test_id']}/start", headers=self.h).json()["data"]
        self.aid, self.total, self.n = d["attempt_id"], d["overview"]["total"], 1

    @task(5)
    def answer_next(self):
        q = self.client.get(f"/api/v1/attempts/{self.aid}/question?n={self.n}", headers=self.h,
                            name="/attempts/[id]/question").json()["data"]
        self.client.put(f"/api/v1/attempts/{self.aid}/answers/{q['question_id']}", headers=self.h,
                        json={"selected": random.randint(0, 3)}, name="/attempts/[id]/answers/[qid]")
        self.n = self.n % self.total + 1

    @task(1)
    def overview(self):
        self.client.get(f"/api/v1/attempts/{self.aid}/overview", headers=self.h, name="/attempts/[id]/overview")

    @task(1)
    def violation(self):
        self.client.post(f"/api/v1/attempts/{self.aid}/violations", headers=self.h,
                         json={"events": [{"type": "resize"}]}, name="/attempts/[id]/violations")
