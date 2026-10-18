from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"


def pool_test(client, admin, pool=12, n=4, cutoff=2, **kw):
    tid = make_test(client, admin, "aptitude", cutoff=cutoff, questions_per_attempt=n, **kw)
    for i in range(pool):
        add_q(client, admin, tid, mcq(body=f"Q{i}"))
    return tid


def start(client, admin, tid, email):
    cid = make_candidate(client, admin, email=email)
    assign(client, admin, tid, cid)
    h = login(client, email, "Password123")
    return cid, h, client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]


def served(client, h, aid, total):
    return [client.get(f"{API}/attempts/{aid}/question?n={n}", headers=h).json()["data"] for n in range(1, total + 1)]


def test_each_candidate_gets_n_questions_and_sets_differ(client, admin):
    tid = pool_test(client, admin)
    sets = []
    for i in range(6):
        cid, h, d = start(client, admin, tid, f"p{i}@x.com")
        assert d["overview"]["total"] == 4
        qs = served(client, h, d["attempt_id"], 4)
        ids = [q["question_id"] for q in qs]
        assert len(set(ids)) == 4
        sets.append(frozenset(ids))
    assert len(set(sets)) > 1  # not everyone got the same set (12 choose 4 = 495 possibilities)


def test_beyond_n_is_404_and_resume_keeps_same_questions(client, admin):
    tid = pool_test(client, admin)
    cid, h, d = start(client, admin, tid, "p@x.com")
    aid = d["attempt_id"]
    first = [q["question_id"] for q in served(client, h, aid, 4)]
    assert client.get(f"{API}/attempts/{aid}/question?n=5", headers=h).status_code == 404
    again = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]   # resume
    assert again["attempt_id"] == aid
    assert [q["question_id"] for q in served(client, h, aid, 4)] == first


def test_cannot_answer_a_question_you_were_not_served(client, admin):
    tid = pool_test(client, admin)
    cid, h, d = start(client, admin, tid, "p@x.com")
    mine = {q["question_id"] for q in served(client, h, d["attempt_id"], 4)}
    other = next(q["id"] for q in client.get(f"{API}/admin/tests/{tid}/questions", headers=admin).json()["data"] if q["id"] not in mine)
    r = client.put(f"{API}/attempts/{d['attempt_id']}/answers/{other}", headers=h, json={"selected": 0})
    assert r.status_code == 404


def test_scoring_counts_only_served_questions(client, admin):
    tid = pool_test(client, admin, cutoff=4, show_result=True)
    cid, h, d = start(client, admin, tid, "p@x.com")
    aid = d["attempt_id"]
    for q in served(client, h, aid, 4):
        client.put(f"{API}/attempts/{aid}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index("4")})
    r = client.post(f"{API}/attempts/{aid}/submit", headers=h).json()["data"]
    assert r["score"] == 4 and r["max_score"] == 4 and r["passed"] is True   # not 4 / 12
    a = client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]["stages"][0]["attempt"]
    assert len(a["breakdown"]) == 4


def test_summary_rules_for_pool(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1, questions_per_attempt=5)
    for i in range(3):
        add_q(client, admin, tid, mcq(body=f"Q{i}"))
    s = client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]
    assert any("larger than the pool" in e for e in s["errors"]) and not s["ready"]
    client.put(f"{API}/admin/tests/{tid}", headers=admin, json={"title": "t", "stage": "aptitude", "duration_min": 30, "cutoff_score": 1, "questions_per_attempt": 2})
    s = client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]
    assert s["ready"] and s["per_candidate"] == {"questions": 2, "min_marks": 2, "max_marks": 2}
    # cutoff above what the smallest draw can reach
    client.put(f"{API}/admin/tests/{tid}", headers=admin, json={"title": "t", "stage": "aptitude", "duration_min": 30, "cutoff_score": 3, "questions_per_attempt": 2})
    assert any("smallest possible total" in e for e in client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]["errors"])


def test_partial_update_does_not_reset_pool_size(client, admin):
    tid = make_test(client, admin, "aptitude", questions_per_attempt=3)
    client.put(f"{API}/admin/tests/{tid}", headers=admin, json={"title": "renamed", "stage": "aptitude", "duration_min": 30, "cutoff_score": 1})
    assert client.get(f"{API}/admin/tests/{tid}", headers=admin).json()["data"]["questions_per_attempt"] == 3


def test_no_pool_means_everyone_gets_all(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1)
    for i in range(5):
        add_q(client, admin, tid, mcq(body=f"Q{i}"))
    cid, h, d = start(client, admin, tid, "p@x.com")
    assert d["overview"]["total"] == 5
