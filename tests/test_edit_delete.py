from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"


def attempt_for(client, h, tid, text="4"):
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    q = d["first_question"]
    client.put(f"{API}/attempts/{d['attempt_id']}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index(text)})
    client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)
    return d["attempt_id"]


def setup(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1)
    add_q(client, admin, tid, mcq())
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    return tid, cid, login(client, "c1@x.com", "Password123")


def test_edit_candidate_and_email_conflict(client, admin):
    tid, cid, h = setup(client, admin)
    make_candidate(client, admin, email="other@x.com")
    body = {"email": "new@x.com", "name": "New Name", "college": "C", "branch": "B", "batch": "B9", "is_active": True}
    r = client.put(f"{API}/admin/candidates/{cid}", headers=admin, json=body)
    assert r.status_code == 200 and r.json()["data"]["name"] == "New Name"
    login(client, "new@x.com", "Password123")
    assert client.put(f"{API}/admin/candidates/{cid}", headers=admin, json={**body, "email": "other@x.com"}).status_code == 409


def test_deactivate_blocks_login_and_existing_token(client, admin):
    tid, cid, h = setup(client, admin)
    body = {"email": "c1@x.com", "name": "c1", "is_active": False}
    client.put(f"{API}/admin/candidates/{cid}", headers=admin, json=body)
    assert client.get(f"{API}/tests", headers=h).status_code == 401           # old token dies at once
    assert client.post(f"{API}/auth/login", data={"username": "c1@x.com", "password": "Password123"}).status_code == 401


def test_reset_password(client, admin):
    tid, cid, h = setup(client, admin)
    r = client.post(f"{API}/admin/candidates/{cid}/reset-password", headers=admin).json()["data"]
    assert client.post(f"{API}/auth/login", data={"username": "c1@x.com", "password": "Password123"}).status_code == 401
    h2 = login(client, "c1@x.com", r["password"])
    assert client.get(f"{API}/auth/me", headers=h2).json()["data"]["must_change_password"] is True


def test_delete_candidate_removes_everything(client, admin, db_session):
    from app.models import Answer, Attempt, CandidateTest, User
    tid, cid, h = setup(client, admin)
    attempt_for(client, h, tid)
    assert client.delete(f"{API}/admin/candidates/{cid}", headers=admin).status_code == 200
    with db_session() as s:
        assert s.query(Attempt).count() == 0 and s.query(Answer).count() == 0
        assert s.query(CandidateTest).count() == 0 and s.query(User).filter_by(email="c1@x.com").count() == 0
    assert client.post(f"{API}/auth/login", data={"username": "c1@x.com", "password": "Password123"}).status_code == 401
    assert client.delete(f"{API}/admin/candidates/{cid}", headers=admin).status_code == 404
    # the test itself survives
    assert client.get(f"{API}/admin/tests/{tid}", headers=admin).status_code == 200


def test_bulk_delete_and_rbac(client, admin):
    ids = [make_candidate(client, admin, email=f"b{i}@x.com") for i in range(3)]
    h = login(client, "b0@x.com", "Password123")
    assert client.post(f"{API}/admin/candidates/bulk-delete", headers=h, json={"ids": ids}).status_code == 403
    assert client.post(f"{API}/admin/candidates/bulk-delete", headers=admin, json={"ids": ids[:2]}).json()["data"]["deleted"] == 2
    assert client.get(f"{API}/admin/candidates", headers=admin).json()["meta"]["total"] == 1


def test_force_delete_test_with_attempts_and_state_recompute(client, admin):
    tid, cid, h = setup(client, admin)
    attempt_for(client, h, tid)
    r = client.delete(f"{API}/admin/tests/{tid}", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["details"]["attempts"] == 1
    assert client.get(f"{API}/admin/tests/{tid}", headers=admin).status_code == 200  # untouched
    r = client.delete(f"{API}/admin/tests/{tid}?force=true", headers=admin)
    assert r.status_code == 200 and r.json()["data"]["attempts_removed"] == 1
    assert client.get(f"{API}/admin/tests/{tid}", headers=admin).status_code == 404
    me = client.get(f"{API}/auth/me", headers=h).json()["data"]
    assert me["candidate"]["state"] == "REGISTERED"  # no longer stuck on a result for a deleted test


def test_allow_retake(client, admin):
    tid, cid, h = setup(client, admin)
    attempt_for(client, h, tid, text="3")  # fail
    assert client.get(f"{API}/auth/me", headers=h).json()["data"]["candidate"]["state"] == "CANDIDATE_REJECTED"
    assert client.post(f"{API}/tests/{tid}/start", headers=h).status_code == 409
    assert client.post(f"{API}/admin/candidates/{cid}/tests/{tid}/retake", headers=admin).status_code == 200
    assert client.get(f"{API}/auth/me", headers=h).json()["data"]["candidate"]["state"] == "REGISTERED"
    attempt_for(client, h, tid, text="4")
    assert client.get(f"{API}/auth/me", headers=h).json()["data"]["candidate"]["state"] == "FINAL_RESULT"
    assert client.post(f"{API}/admin/candidates/{cid}/tests/{tid}/retake", headers=admin).status_code == 200
    assert client.post(f"{API}/admin/candidates/{cid}/tests/{tid}/retake", headers=admin).status_code == 404


def test_unassign_rules(client, admin):
    tid, cid, h = setup(client, admin)
    attempt_for(client, h, tid)
    assert client.delete(f"{API}/admin/candidates/{cid}/tests/{tid}", headers=admin).status_code == 409
    client.post(f"{API}/admin/candidates/{cid}/tests/{tid}/retake", headers=admin)
    assert client.delete(f"{API}/admin/candidates/{cid}/tests/{tid}", headers=admin).status_code == 200
    assert client.get(f"{API}/tests", headers=h).json()["data"] == []


def test_disqualify_and_reinstate(client, admin):
    tid, cid, h = setup(client, admin)
    client.post(f"{API}/admin/candidates/{cid}/disqualify", headers=admin)
    assert client.post(f"{API}/admin/candidates/{cid}/reinstate", headers=admin).json()["data"]["state"] == "REGISTERED"
    assert client.post(f"{API}/admin/candidates/{cid}/reinstate", headers=admin).status_code == 409
    attempt_for(client, h, tid)  # can take the test again
