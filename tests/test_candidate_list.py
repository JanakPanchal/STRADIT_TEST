from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"


def take(client, email, tid, answer_text):
    h = login(client, email, "Password123")
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    q = d["first_question"]
    if answer_text:
        client.put(f"{API}/attempts/{d['attempt_id']}/answers/{q['question_id']}", headers=h,
                   json={"selected": q["options"].index(answer_text)})
    client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)


def world(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1)
    add_q(client, admin, tid, mcq())
    ids = {}
    for em, batch in (("pass@x.com", "B1"), ("fail@x.com", "B1"), ("idle@x.com", "B2")):
        ids[em] = make_candidate(client, admin, email=em, batch=batch)
        assign(client, admin, tid, ids[em])
    take(client, "pass@x.com", tid, "4")
    take(client, "fail@x.com", tid, "3")
    return tid, ids


def rows(client, admin, **params):
    r = client.get(f"{API}/admin/candidates", headers=admin, params=params).json()
    return {x["email"]: x for x in r["data"]}, r["meta"]


def test_outcomes_stage_cells_and_counts(client, admin):
    tid, ids = world(client, admin)
    data, meta = rows(client, admin)
    assert data["pass@x.com"]["stages"]["aptitude"]["status"] == "cleared"
    assert data["pass@x.com"]["stages"]["aptitude"]["score"] == 1
    assert data["fail@x.com"]["outcome"] == "not_cleared" and data["fail@x.com"]["stages"]["aptitude"]["status"] == "not_cleared"
    assert data["idle@x.com"]["stages"]["aptitude"]["status"] == "assigned" and data["idle@x.com"]["stages"]["behaviour"]["status"] == "not_assigned"
    assert data["pass@x.com"]["outcome"] == "cleared"  # cleared every stage assigned to them
    assert meta["counts"] == {"cleared": 1, "not_cleared": 1, "in_progress": 0, "not_started": 1, "disqualified": 0, "all": 3, "never_signed_in": 1}  # idle@ never signed in


def test_filters(client, admin):
    tid, ids = world(client, admin)
    assert set(rows(client, admin, outcome="not_cleared")[0]) == {"fail@x.com"}
    assert set(rows(client, admin, stage="aptitude", stage_result="cleared")[0]) == {"pass@x.com"}
    assert set(rows(client, admin, stage="aptitude", stage_result="not_started")[0]) == {"idle@x.com"}
    assert set(rows(client, admin, batch="B1")[0]) == {"pass@x.com", "fail@x.com"}
    assert set(rows(client, admin, search="idle")[0]) == {"idle@x.com"}
    assert set(rows(client, admin, test_id=tid)[0]) == {"pass@x.com", "fail@x.com", "idle@x.com"}
    d, meta = rows(client, admin, batch="B1", outcome="cleared")
    assert set(d) == {"pass@x.com"} and meta["counts"]["not_cleared"] == 1  # tiles ignore the outcome filter itself
    d, _ = rows(client, admin, batch="B2", outcome="cleared")
    assert d == {}


def test_sorting_and_bad_filter_value(client, admin):
    world(client, admin)
    r = client.get(f"{API}/admin/candidates", headers=admin, params={"sort": "score"}).json()["data"]
    assert r[0]["email"] == "pass@x.com"
    assert client.get(f"{API}/admin/candidates", headers=admin, params={"outcome": "bogus"}).status_code == 422


def test_filter_options_and_export(client, admin):
    world(client, admin)
    f = client.get(f"{API}/admin/candidates/filters", headers=admin).json()["data"]
    assert f["batches"] == ["B1", "B2"] and f["tests"][0]["stage"] == "aptitude"
    r = client.get(f"{API}/admin/candidates/export", headers=admin, params={"outcome": "not_cleared"})
    assert r.headers["content-type"].startswith("text/csv") and "fail@x.com" in r.text and "pass@x.com" not in r.text
    assert "aptitude_result" in r.text.splitlines()[0]


def test_disqualified_outcome(client, admin):
    tid, ids = world(client, admin)
    client.post(f"{API}/admin/candidates/{ids['idle@x.com']}/disqualify", headers=admin)
    d, meta = rows(client, admin, outcome="disqualified")
    assert set(d) == {"idle@x.com"} and meta["counts"]["disqualified"] == 1


def test_cleared_only_after_all_assigned_stages(client, admin):
    apt = make_test(client, admin, "aptitude", cutoff=1)
    beh = make_test(client, admin, "behaviour", cutoff=1)
    add_q(client, admin, apt, mcq())
    add_q(client, admin, beh, {"type": "likert", "body": "S", "options": [{"text": "a", "score": 1}, {"text": "b", "score": 5}]})
    cid = make_candidate(client, admin, email="two@x.com")
    assign(client, admin, apt, cid)
    assign(client, admin, beh, cid)
    take(client, "two@x.com", apt, "4")
    d, _ = rows(client, admin)
    assert d["two@x.com"]["outcome"] == "in_progress"  # behaviour still to do
    h = login(client, "two@x.com", "Password123")
    r = client.post(f"{API}/tests/{beh}/start", headers=h).json()["data"]
    q = r["first_question"]
    client.put(f"{API}/attempts/{r['attempt_id']}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index("b")})
    client.post(f"{API}/attempts/{r['attempt_id']}/submit", headers=h)
    assert rows(client, admin)[0]["two@x.com"]["outcome"] == "cleared"
