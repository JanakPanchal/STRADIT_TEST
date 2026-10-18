import time
from datetime import timedelta

from app.core.security import utcnow
from app.models import Attempt
from app.services.attempts import sweep_expired
from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"


def setup_apt(client, admin, cutoff=2, nq=3, **kw):
    tid = make_test(client, admin, "aptitude", cutoff=cutoff, **kw)
    for i in range(nq):
        add_q(client, admin, tid, mcq(body=f"Q{i}: 2+2?"))
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    return tid, cid, login(client, "c1@x.com", "Password123")


def start(client, h, tid):
    return client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]


def answer_all_correct(client, h, aid, total, text="4"):
    for n in range(1, total + 1):
        q = client.get(f"{API}/attempts/{aid}/question?n={n}", headers=h).json()["data"]
        r = client.put(f"{API}/attempts/{aid}/answers/{q['question_id']}", headers=h,
                       json={"selected": q["options"].index(text)})
        assert r.status_code == 200


def stage_attempt(client, admin, cid, i=0):
    return client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]["stages"][i]["attempt"]


def test_candidate_view_never_leaks_answers(client, admin):
    tid, cid, h = setup_apt(client, admin)
    d = start(client, h, tid)
    q = d["first_question"]
    assert "correct_option" not in q and sorted(q["options"]) == ["3", "4", "5", "6"]
    assert d["overview"]["total"] == 3


def test_full_pass_flow_scoring_with_shuffle(client, admin):
    tid, cid, h = setup_apt(client, admin, cutoff=3)
    aid = start(client, h, tid)["attempt_id"]
    answer_all_correct(client, h, aid, 3)
    r = client.post(f"{API}/attempts/{aid}/submit", headers=h).json()["data"]
    assert r["status"] == "submitted" and "score" not in r
    att = stage_attempt(client, admin, cid)
    assert att["score"] == 3 and att["passed"] is True
    assert client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]["candidate"]["state"] == "FINAL_RESULT"


def test_question_order_stable_within_attempt(client, admin):
    tid, cid, h = setup_apt(client, admin, nq=8)
    aid = start(client, h, tid)["attempt_id"]
    get = lambda: [client.get(f"{API}/attempts/{aid}/question?n={n}", headers=h).json()["data"]["question_id"] for n in range(1, 9)]
    a, b = get(), get()
    assert a == b and len(set(a)) == 8


def test_show_result_flag(client, admin):
    tid, cid, h = setup_apt(client, admin, cutoff=1, show_result=True)
    aid = start(client, h, tid)["attempt_id"]
    answer_all_correct(client, h, aid, 3)
    r = client.post(f"{API}/attempts/{aid}/submit", headers=h).json()["data"]
    assert r["score"] == 3 and r["passed"] is True


def test_cutoff_gate_and_rejection(client, admin):
    apt = make_test(client, admin, "aptitude", cutoff=3)
    beh = make_test(client, admin, "behaviour", cutoff=1)
    for i in range(3):
        add_q(client, admin, apt, mcq(body=f"Q{i}"))
    add_q(client, admin, beh, {"type": "likert", "body": "S", "options": [{"text": "No", "score": 1}, {"text": "Yes", "score": 5}]})
    cid = make_candidate(client, admin)
    assign(client, admin, apt, cid)
    assign(client, admin, beh, cid)
    h = login(client, "c1@x.com", "Password123")
    tests = {t["stage"]: t for t in client.get(f"{API}/tests", headers=h).json()["data"]}
    assert tests["aptitude"]["status"] == "available" and tests["behaviour"]["status"] == "locked"
    assert client.post(f"{API}/tests/{beh}/start", headers=h).status_code == 403
    aid = start(client, h, apt)["attempt_id"]
    client.post(f"{API}/attempts/{aid}/submit", headers=h)  # nothing answered -> fail
    assert client.post(f"{API}/tests/{beh}/start", headers=h).status_code == 403
    assert client.get(f"{API}/auth/me", headers=h).json()["data"]["candidate"]["state"] == "CANDIDATE_REJECTED"


def test_pass_unlocks_behaviour_likert_scoring(client, admin):
    apt = make_test(client, admin, "aptitude", cutoff=1)
    beh = make_test(client, admin, "behaviour", cutoff=4)
    add_q(client, admin, apt, mcq())
    add_q(client, admin, beh, {"type": "likert", "body": "S", "options": [{"text": "Disagree", "score": 1}, {"text": "Agree", "score": 5}]})
    cid = make_candidate(client, admin)
    assign(client, admin, apt, cid)
    assign(client, admin, beh, cid)
    h = login(client, "c1@x.com", "Password123")
    a = start(client, h, apt)["attempt_id"]
    answer_all_correct(client, h, a, 1)
    client.post(f"{API}/attempts/{a}/submit", headers=h)
    d = start(client, h, beh)
    q = d["first_question"]
    client.put(f"{API}/attempts/{d['attempt_id']}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index("Agree")})
    client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)
    res = client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]
    assert res["stages"][1]["attempt"]["score"] == 5 and res["candidate"]["state"] == "FINAL_RESULT"


def test_ownership(client, admin):
    tid, cid, h = setup_apt(client, admin)
    aid = start(client, h, tid)["attempt_id"]
    make_candidate(client, admin, email="c2@x.com")
    h2 = login(client, "c2@x.com", "Password123")
    for m, p in [("get", f"/attempts/{aid}/question?n=1"), ("get", f"/attempts/{aid}/overview"),
                 ("post", f"/attempts/{aid}/submit"), ("get", f"/attempts/{aid}/result")]:
        assert getattr(client, m)(API + p, headers=h2).status_code == 404
    assert client.post(f"{API}/tests/{tid}/start", headers=h2).status_code == 404


def test_start_resumable_single_attempt(client, admin):
    tid, cid, h = setup_apt(client, admin)
    d1, d2 = start(client, h, tid), start(client, h, tid)
    assert d1["attempt_id"] == d2["attempt_id"] and d2["resumed"] is True
    client.post(f"{API}/attempts/{d1['attempt_id']}/submit", headers=h)
    assert client.post(f"{API}/tests/{tid}/start", headers=h).status_code == 409


def test_submit_idempotent_and_closed_rejects_answers(client, admin):
    tid, cid, h = setup_apt(client, admin)
    aid = start(client, h, tid)["attempt_id"]
    assert client.post(f"{API}/attempts/{aid}/submit", headers=h).status_code == 200
    assert client.post(f"{API}/attempts/{aid}/submit", headers=h).status_code == 200
    assert client.put(f"{API}/attempts/{aid}/answers/1", headers=h, json={"selected": 0}).status_code == 409


def test_server_timer_expiry(client, admin, db_session):
    tid, cid, h = setup_apt(client, admin, cutoff=1)
    aid = start(client, h, tid)["attempt_id"]
    answer_all_correct(client, h, aid, 1)
    with db_session() as s:
        s.get(Attempt, aid).server_deadline = utcnow() - timedelta(minutes=1)
        s.commit()
    assert client.put(f"{API}/attempts/{aid}/answers/1", headers=h, json={"selected": 0}).status_code == 409
    att = stage_attempt(client, admin, cid)
    assert att["status"] == "expired" and att["submit_reason"] == "timeout" and att["score"] == 1


def test_sweeper(client, admin, db_session):
    tid, cid, h = setup_apt(client, admin)
    aid = start(client, h, tid)["attempt_id"]
    with db_session() as s:
        s.get(Attempt, aid).server_deadline = utcnow() - timedelta(minutes=5)
        s.commit()
        assert sweep_expired(s) == 1 and sweep_expired(s) == 0
        assert s.get(Attempt, aid).status == "expired"


def test_deadline_capped_by_window_end(client, admin):
    tid, cid, h = setup_apt(client, admin, duration=60, end_at=(utcnow() + timedelta(minutes=5)).isoformat())
    assert start(client, h, tid)["overview"]["remaining_seconds"] <= 300


def test_window_not_open(client, admin):
    tid, cid, h = setup_apt(client, admin, start_at=(utcnow() + timedelta(hours=1)).isoformat())
    assert client.post(f"{API}/tests/{tid}/start", headers=h).status_code == 403
    assert client.get(f"{API}/tests", headers=h).json()["data"][0]["status"] == "not_open"


def test_violation_clustering_and_auto_submit(client, admin):
    tid, cid, h = setup_apt(client, admin, max_violations=2)
    aid = start(client, h, tid)["attempt_id"]
    r = client.post(f"{API}/attempts/{aid}/violations", headers=h, json={"events": [
        {"type": "tab_switch"}, {"type": "blur"}, {"type": "fullscreen_exit"}, {"type": "resize"}]}).json()["data"]
    assert r["violation_count"] == 1 and not r["auto_submitted"]
    time.sleep(2.1)
    r = client.post(f"{API}/attempts/{aid}/violations", headers=h,
                    json={"events": [{"type": "tab_switch", "client_ts": "1999-01-01"}]}).json()["data"]
    assert r["violation_count"] == 2 and r["auto_submitted"]
    assert client.post(f"{API}/attempts/{aid}/violations", headers=h, json={"events": [{"type": "blur"}]}).status_code == 409
    v = stage_attempt(client, admin, cid)
    assert v["submit_reason"] == "violation_limit" and len(v["violations"]) == 5
    assert sum(x["counted"] for x in v["violations"]) == 2


def test_invalid_violation_type(client, admin):
    tid, cid, h = setup_apt(client, admin)
    aid = start(client, h, tid)["attempt_id"]
    assert client.post(f"{API}/attempts/{aid}/violations", headers=h, json={"events": [{"type": "nope"}]}).status_code == 422


def test_admin_disqualify(client, admin):
    tid, cid, h = setup_apt(client, admin)
    aid = start(client, h, tid)["attempt_id"]
    assert client.post(f"{API}/admin/candidates/{cid}/disqualify", headers=admin).status_code == 200
    assert client.put(f"{API}/attempts/{aid}/answers/1", headers=h, json={"selected": 0}).status_code == 409
    res = client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]
    assert res["candidate"]["state"] == "DISQUALIFIED" and res["stages"][0]["attempt"]["status"] == "disqualified"


def test_answer_range_and_clear(client, admin):
    tid, cid, h = setup_apt(client, admin)
    d = start(client, h, tid)
    aid, qid = d["attempt_id"], d["first_question"]["question_id"]
    assert client.put(f"{API}/attempts/{aid}/answers/{qid}", headers=h, json={"selected": 9}).status_code == 422
    client.put(f"{API}/attempts/{aid}/answers/{qid}", headers=h, json={"selected": 0})
    assert client.get(f"{API}/attempts/{aid}/question?n=1", headers=h).json()["data"]["selected"] == 0
    client.put(f"{API}/attempts/{aid}/answers/{qid}", headers=h, json={"selected": None})
    assert client.get(f"{API}/attempts/{aid}/question?n=1", headers=h).json()["data"]["selected"] is None
