from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"


def college(client, admin, name, code):
    return client.post(f"{API}/admin/colleges", headers=admin, json={"name": name, "code": code}).json()["data"]["id"]


def take(client, h, tid, plan):
    """plan: {question index: option text or None}. Questions are shuffled per candidate, so answer by body text."""
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    aid, total = d["attempt_id"], d["overview"]["total"]
    for n in range(1, total + 1):
        q = client.get(f"{API}/attempts/{aid}/question?n={n}", headers=h).json()["data"]
        text = plan.get(int(q["body"][1:]))
        if text:
            client.put(f"{API}/attempts/{aid}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index(text)})
    client.post(f"{API}/attempts/{aid}/submit", headers=h)


def world(client, admin):
    a, b = college(client, admin, "College A", "CA"), college(client, admin, "College B", "CB")
    tid = make_test(client, admin, "aptitude", cutoff=3)
    for i in range(6):
        add_q(client, admin, tid, mcq(body=f"Q{i}"))
    for i in range(8):
        r = client.post(f"{API}/admin/candidates", headers=admin, json={"email": f"c{i}@x.com", "name": f"C{i}", "college_id": a if i < 4 else b,
                                                                   "batch": "B1" if i < 4 else "B2", "password": "Password123"}).json()["data"]
        assign(client, admin, tid, r["id"])
        strong = i < 4
        plan = {0: "4", 1: "5", 2: "4" if i < 4 else "3", 3: "4" if i < 2 else None, 4: "4" if strong else "6", 5: "4" if strong else "6"}
        take(client, login(client, f"c{i}@x.com", "Password123"), tid, plan)
    return tid


def test_outcome_distribution_and_breakdowns(client, admin):
    tid = world(client, admin)
    r = client.get(f"{API}/admin/tests/{tid}/analytics", headers=admin).json()["data"]
    assert (r["assigned"], r["completed"], r["in_progress"], r["not_started"]) == (8, 8, 0, 0)
    assert r["pass_rate"] == 50.0 and r["top_percent"] == 83.3
    assert r["histogram"]["bins"][8] == 2 and r["histogram"]["bins"][6] == 2 and r["histogram"]["bins"][1] == 4
    assert r["histogram"]["below_cutoff"][1] == 4 and r["histogram"]["below_cutoff"][8] == 0
    col = {c["name"]: c for c in r["by_college"]}
    assert col["College A"]["pass_rate"] == 100.0 and col["College B"]["pass_rate"] == 0.0 and col["College A"]["completed"] == 4
    assert {b["name"] for b in r["by_batch"]} == {"B1", "B2"}
    assert r["avg_minutes"] is not None and r["avg_violations"] == 0


def test_item_analysis_flags(client, admin):
    tid = world(client, admin)
    items = {i["body"]: i for i in client.get(f"{API}/admin/tests/{tid}/analytics", headers=admin).json()["data"]["items"]}
    q0, q1, q2, q3 = items["Q0"], items["Q1"], items["Q2"], items["Q3"]
    assert q0["correct_rate"] == 1.0 and "easy" in [f[0] for f in q0["flags"]]
    assert q1["correct_rate"] == 0.0 and {"hard", "key"} <= {f[0] for f in q1["flags"]}      # everyone picked the same WRONG option
    assert q2["correct_rate"] == 0.5 and q2["flags"] == []
    assert q3["correct_rate"] == 0.25 and q3["skipped"] == 6
    wrong = next(o for o in q1["options"] if o["text"] == "5")
    assert wrong["chosen"] == 8 and wrong["correct"] is False
    assert sum(o["chosen"] for o in q2["options"]) == q2["answered"] == 8


def test_empty_and_access(client, admin):
    tid = make_test(client, admin, "aptitude")
    r = client.get(f"{API}/admin/tests/{tid}/analytics", headers=admin).json()["data"]
    assert r["empty"] is True and r["completed"] == 0
    client.post(f"{API}/auth/register", json={"email": "a@x.com", "password": "Password123", "name": "A"})
    h = login(client, "a@x.com", "Password123")
    assert client.get(f"{API}/admin/tests/{tid}/analytics", headers=h).status_code == 403
    assert client.get(f"{API}/admin/tests/9999/analytics", headers=admin).status_code == 404


def test_pool_denominator_is_candidates_served(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=0, questions_per_attempt=3)
    for i in range(12):
        add_q(client, admin, tid, mcq(body=f"Q{i}"))
    for i in range(6):
        cid = make_candidate(client, admin, email=f"p{i}@x.com")
        assign(client, admin, tid, cid)
        h = login(client, f"p{i}@x.com", "Password123")
        d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
        for n in range(1, 4):
            q = client.get(f"{API}/attempts/{d['attempt_id']}/question?n={n}", headers=h).json()["data"]
            client.put(f"{API}/attempts/{d['attempt_id']}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index("4")})
        client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)
    r = client.get(f"{API}/admin/tests/{tid}/analytics", headers=admin).json()["data"]
    assert sum(i["served"] for i in r["items"]) == 18                      # 6 candidates x 3 questions, not 6 x 12
    assert all(i["correct_rate"] == 1.0 for i in r["items"] if i["served"])  # judged only on those who saw them
    assert not any(i["flags"] for i in r["items"] if i["served"] < 5)       # too few observations to judge
