from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"
CODING = {"type": "coding", "body": "Print 42", "marks": 10,
          "test_cases": [{"input": "", "expected_output": "42", "is_sample": True},
                         {"input": "", "expected_output": "42", "is_sample": False}]}


def start(client, admin):
    tid = make_test(client, admin, "coding", cutoff=1)
    add_q(client, admin, tid, CODING)
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    h = login(client, "c1@x.com", "Password123")
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    return h, d["attempt_id"], d["first_question"]["question_id"]


def body(qid, **kw):
    return {"question_id": qid, "language": "python", "source_code": "print(42)", **kw}


def test_custom_run_returns_output_and_counts_against_budget(client, admin):
    h, aid, qid = start(client, admin)
    r = client.post(f"{API}/attempts/{aid}/code/run-custom", headers=h, json=body(qid, stdin="hello")).json()["data"]
    assert r["ok"] is True and r["stdout"] == "ran:hello" and r["time_ms"] == 7 and r["memory_kb"] == 900
    for _ in range(19):  # budget is 20 runs per minute per attempt
        assert client.post(f"{API}/attempts/{aid}/code/run-custom", headers=h, json=body(qid)).status_code == 200
    assert client.post(f"{API}/attempts/{aid}/code/run-custom", headers=h, json=body(qid)).status_code == 429
    assert client.post(f"{API}/attempts/{aid}/code/run", headers=h, json=body(qid)).status_code == 429  # shared budget


def test_sample_run_now_reports_memory(client, admin):
    h, aid, qid = start(client, admin)
    c = client.post(f"{API}/attempts/{aid}/code/run", headers=h, json=body(qid)).json()["data"]["cases"][0]
    assert c["memory_kb"] == 1000 and c["time_ms"] == 5


def test_drafts_autosave_per_language_and_come_back_with_the_question(client, admin):
    h, aid, qid = start(client, admin)
    q = client.get(f"{API}/attempts/{aid}/question?n=1", headers=h).json()["data"]
    assert q["drafts"] == {} and q["last_language"] is None
    for lang, code in (("python", "print(1)"), ("java", "class Main {}"), ("python", "print(2)")):
        assert client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json=body(qid, language=lang, source_code=code)).status_code == 200
    q = client.get(f"{API}/attempts/{aid}/question?n=1", headers=h).json()["data"]
    assert q["drafts"] == {"python": "print(2)", "java": "class Main {}"}   # upsert, not duplicate
    assert q["last_language"] == "python"
    # clearing the editor is a valid draft
    assert client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json={"question_id": qid, "language": "python", "source_code": ""}).status_code == 200


def test_draft_rules(client, admin):
    h, aid, qid = start(client, admin)
    assert client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json=body(qid, language="cobol")).status_code == 422
    assert client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json=body(9999)).status_code == 404
    assert client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json=body(qid, source_code="x" * 70_000)).status_code == 422
    client.post(f"{API}/attempts/{aid}/submit", headers=h)
    assert client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json=body(qid)).status_code == 409


def test_other_candidate_cannot_touch_my_playground(client, admin):
    h, aid, qid = start(client, admin)
    make_candidate(client, admin, email="c2@x.com")
    h2 = login(client, "c2@x.com", "Password123")
    for m, p, kw in (("put", f"/attempts/{aid}/code/draft", {"json": body(qid)}),
                     ("post", f"/attempts/{aid}/code/run-custom", {"json": body(qid)}),
                     ("get", f"/attempts/{aid}/code/submissions?question_id={qid}", {})):
        assert getattr(client, m)(API + p, headers=h2, **kw).status_code == 404


def test_submission_history_hides_hidden_case_detail(client, admin):
    h, aid, qid = start(client, admin)
    client.post(f"{API}/attempts/{aid}/code/submit", headers=h, json=body(qid))
    client.post(f"{API}/attempts/{aid}/code/submit", headers=h, json=body(qid, source_code="print(0)"))
    rows = client.get(f"{API}/attempts/{aid}/code/submissions?question_id={qid}", headers=h).json()["data"]
    assert [r["passed_cases"] for r in rows] == [0, 2] and rows[1]["total_cases"] == 2   # newest first
    assert "source_code" not in rows[0] and "expected" not in str(rows[0])


def test_draft_timestamps_for_offline_recovery(client, admin):
    h, aid, qid = start(client, admin)
    client.put(f"{API}/attempts/{aid}/code/draft", headers=h, json=body(qid))
    q = client.get(f"{API}/attempts/{aid}/question?n=1", headers=h).json()["data"]
    assert q["draft_times"]["python"].endswith("Z")


def test_can_load_own_previous_submission_but_not_someone_elses(client, admin):
    h, aid, qid = start(client, admin)
    client.post(f"{API}/attempts/{aid}/code/submit", headers=h, json=body(qid, source_code="print('mine')  # 42"))
    sid = client.get(f"{API}/attempts/{aid}/code/submissions?question_id={qid}", headers=h).json()["data"][0]["id"]
    r = client.get(f"{API}/attempts/{aid}/code/submissions/{sid}", headers=h).json()["data"]
    assert r["source_code"] == "print('mine')  # 42" and r["language"] == "python"
    make_candidate(client, admin, email="c2@x.com")
    h2 = login(client, "c2@x.com", "Password123")
    assert client.get(f"{API}/attempts/{aid}/code/submissions/{sid}", headers=h2).status_code == 404
    assert client.get(f"{API}/attempts/{aid}/code/submissions/9999", headers=h).status_code == 404
