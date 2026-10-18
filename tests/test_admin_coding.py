import io

from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"

CODING = {"type": "coding", "body": "Print 42", "marks": 10, "function_signature": "def f():",
          "test_cases": [{"input": "", "expected_output": "42", "is_sample": True},
                         {"input": "", "expected_output": "HIDDEN7", "is_sample": False}]}


def test_coding_flow_hidden_cases_not_leaked(client, admin):
    tid = make_test(client, admin, "coding", cutoff=5)
    add_q(client, admin, tid, CODING)
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    h = login(client, "c1@x.com", "Password123")
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    q = d["first_question"]
    assert len(q["samples"]) == 1 and "HIDDEN7" not in str(q)
    aid = d["attempt_id"]
    body = {"question_id": q["question_id"], "language": "python", "source_code": "print(42)"}
    run = client.post(f"{API}/attempts/{aid}/code/run", headers=h, json=body).json()["data"]
    assert run["cases"][0]["passed"] is True
    sub = client.post(f"{API}/attempts/{aid}/code/submit", headers=h, json=body).json()["data"]
    assert (sub["passed_cases"], sub["total_cases"]) == (1, 2) and "HIDDEN7" not in str(sub)
    client.post(f"{API}/attempts/{aid}/submit", headers=h)
    res = client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]
    a = res["stages"][0]["attempt"]
    assert a["score"] == 5 and a["passed"] is True and res["candidate"]["state"] == "FINAL_RESULT"
    assert len(a["code_submissions"]) == 2


def test_bad_language_and_wrong_question_type(client, admin):
    tid = make_test(client, admin, "coding")
    add_q(client, admin, tid, CODING)
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    h = login(client, "c1@x.com", "Password123")
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    body = {"question_id": d["first_question"]["question_id"], "language": "brainfuck", "source_code": "x"}
    assert client.post(f"{API}/attempts/{d['attempt_id']}/code/run", headers=h, json=body).status_code == 422
    body.update(language="python", question_id=9999)
    assert client.post(f"{API}/attempts/{d['attempt_id']}/code/run", headers=h, json=body).status_code == 404


def test_stage_question_type_mismatch(client, admin):
    tid = make_test(client, admin, "coding")
    assert client.post(f"{API}/admin/tests/{tid}/questions", headers=admin, json=mcq()).status_code == 400


def test_coding_question_needs_hidden_case(client, admin):
    tid = make_test(client, admin, "coding")
    bad = {**CODING, "test_cases": [CODING["test_cases"][0]]}
    assert client.post(f"{API}/admin/tests/{tid}/questions", headers=admin, json=bad).status_code == 422


def test_csv_import_and_atomic_failure(client, admin):
    tid = make_test(client, admin, "aptitude")
    good = "type,body,options,correct,marks,difficulty\nmcq,What is 1+1?,1|2|3|4,2,2,easy\nmcq,Capital?,Paris|Rome,1,1,medium\n"
    r = client.post(f"{API}/admin/tests/{tid}/questions/import", headers=admin, files={"file": ("q.csv", io.BytesIO(good.encode()))})
    assert r.status_code == 201 and r.json()["data"]["imported"] == 2
    bad = "type,body,options,correct\nmcq,Q1,a|b,1\nmcq,Q2,a|b,9\n"
    r = client.post(f"{API}/admin/tests/{tid}/questions/import", headers=admin, files={"file": ("q.csv", io.BytesIO(bad.encode()))})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["row"] == 2
    assert len(client.get(f"{API}/admin/tests/{tid}/questions", headers=admin).json()["data"]) == 2


def test_questions_locked_after_attempts_and_test_delete_guard(client, admin):
    tid = make_test(client, admin, "aptitude")
    add_q(client, admin, tid, mcq())
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    h = login(client, "c1@x.com", "Password123")
    client.post(f"{API}/tests/{tid}/start", headers=h)
    assert client.post(f"{API}/admin/tests/{tid}/questions", headers=admin, json=mcq()).status_code == 409
    assert client.delete(f"{API}/admin/tests/{tid}", headers=admin).status_code == 409


def test_bulk_candidates_and_assign_by_batch(client, admin):
    client.post(f"{API}/admin/colleges", headers=admin, json={"name": "C College", "code": "CC"})
    csv_ = "email,name,college,branch,batch\nx1@x.com,X1,C College,CS,B9\nx2@x.com,X2,C College,CS,B9\nbad,Y,C College,CS,B9\nx1@x.com,Dup,C College,CS,B9\n"
    r = client.post(f"{API}/admin/candidates/bulk", headers=admin, files={"file": ("c.csv", io.BytesIO(csv_.encode()))}).json()["data"]
    assert len(r["created"]) == 2 and len(r["skipped"]) == 2
    pw = r["created"][0]["password"]
    h = login(client, "x1@x.com", pw)
    assert client.get(f"{API}/auth/me", headers=h).json()["data"]["must_change_password"] is True
    tid = make_test(client, admin, "aptitude")
    out = client.post(f"{API}/admin/candidates/assign", headers=admin, json={"test_id": tid, "batch": "B9"}).json()["data"]
    assert out["assigned"] == 2
    again = client.post(f"{API}/admin/candidates/assign", headers=admin, json={"test_id": tid, "batch": "B9"}).json()["data"]
    assert again["assigned"] == 0


def test_export_csv_injection_safe_and_filters(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1)
    add_q(client, admin, tid, mcq())
    r = client.post(f"{API}/admin/candidates", headers=admin, json={"email": "e@x.com", "name": "=HYPERLINK(\"evil\")", "password": "Password123"})
    cid = r.json()["data"]["id"]
    assign(client, admin, tid, cid)
    h = login(client, "e@x.com", "Password123")
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    q = d["first_question"]
    client.put(f"{API}/attempts/{d['attempt_id']}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index("4")})
    client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)
    csv_ = client.get(f"{API}/admin/results/export?test_id={tid}", headers=admin)
    assert csv_.headers["content-type"].startswith("text/csv") and "'=HYPERLINK" in csv_.text
    assert "e@x.com" in client.get(f"{API}/admin/results/export?test_id={tid}&passed_only=true", headers=admin).text
    assert "e@x.com" not in client.get(f"{API}/admin/results/export?test_id={tid}&min_score=5", headers=admin).text


def test_change_password(client, admin):
    make_candidate(client, admin)
    h = login(client, "c1@x.com", "Password123")
    assert client.post(f"{API}/auth/change-password", headers=h, json={"old_password": "nope", "new_password": "NewPass1234"}).status_code == 400
    assert client.post(f"{API}/auth/change-password", headers=h, json={"old_password": "Password123", "new_password": "NewPass1234"}).status_code == 200
    login(client, "c1@x.com", "NewPass1234")
