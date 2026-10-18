import io

from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"


def test_summary_flags_issues_and_publish_gate(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=5)
    s = client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]
    assert not s["ready"] and "Add at least one question" in s["errors"]
    assert client.post(f"{API}/admin/tests/{tid}/publish", headers=admin).status_code == 409
    add_q(client, admin, tid, mcq(marks=2))
    s = client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]
    assert s["total_marks"] == 2 and any("higher than the total" in e for e in s["errors"])  # cutoff 5 > 2
    client.put(f"{API}/admin/tests/{tid}", headers=admin, json={"title": "t", "stage": "aptitude", "duration_min": 30, "cutoff_score": 1})
    assert client.post(f"{API}/admin/tests/{tid}/publish", headers=admin).status_code == 200


def test_draft_hidden_from_candidates_until_published(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1, is_active=False)
    add_q(client, admin, tid, mcq())
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    h = login(client, "c1@x.com", "Password123")
    assert client.get(f"{API}/tests", headers=h).json()["data"] == []
    assert client.post(f"{API}/tests/{tid}/start", headers=h).status_code == 404
    client.post(f"{API}/admin/tests/{tid}/publish", headers=admin)
    assert len(client.get(f"{API}/tests", headers=h).json()["data"]) == 1
    client.post(f"{API}/admin/tests/{tid}/unpublish", headers=admin)
    assert client.get(f"{API}/tests", headers=h).json()["data"] == []


def test_likert_total_marks_uses_max_option_score(client, admin):
    tid = make_test(client, admin, "behaviour", cutoff=3)
    add_q(client, admin, tid, {"type": "likert", "body": "S", "options": [{"text": "a", "score": 1}, {"text": "b", "score": 5}]})
    assert client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]["total_marks"] == 5


def test_import_dry_run_saves_nothing(client, admin):
    tid = make_test(client, admin, "aptitude")
    csv_ = "type,body,options,correct\nmcq,Q1,a|b,1\nmcq,Q2,a|b|c,2\n"
    r = client.post(f"{API}/admin/tests/{tid}/questions/import?dry_run=true", headers=admin, files={"file": ("q.csv", io.BytesIO(csv_.encode()))})
    d = r.json()["data"]
    assert r.status_code == 201 and d["count"] == 2 and d["preview"][1]["options"] == 3
    assert client.get(f"{API}/admin/tests/{tid}/questions", headers=admin).json()["data"] == []


def test_clone_copies_questions_as_draft(client, admin):
    tid = make_test(client, admin, "aptitude")
    add_q(client, admin, tid, mcq())
    new = client.post(f"{API}/admin/tests/{tid}/clone", headers=admin).json()["data"]
    assert new["is_active"] is False and new["title"].endswith("(copy)")
    assert len(client.get(f"{API}/admin/tests/{new['id']}/questions", headers=admin).json()["data"]) == 1


def test_shipped_templates_import_cleanly(client, admin):
    base = "static/templates/"
    apt, beh, cod = (make_test(client, admin, s) for s in ("aptitude", "behaviour", "coding"))
    for tid, name in [(apt, "aptitude_mcq.csv"), (beh, "behaviour_likert.csv"), (cod, "coding.json")]:
        r = client.post(f"{API}/admin/tests/{tid}/questions/import?dry_run=true", headers=admin,
                        files={"file": (name, open(base + name, "rb"))})
        assert r.status_code == 201, (name, r.text)


def test_editing_a_coding_question_keeps_one_question_and_replaces_cases(client, admin):
    tid = make_test(client, admin, "coding", cutoff=1)
    coding = {"type": "coding", "body": "v1", "marks": 5, "test_cases": [
        {"input": "1", "expected_output": "1", "is_sample": True}, {"input": "2", "expected_output": "2", "is_sample": False}]}
    qid = add_q(client, admin, tid, coding)
    edited = {**coding, "body": "v2", "marks": 9, "test_cases": [
        {"input": "7", "expected_output": "7", "is_sample": True}, {"input": "8", "expected_output": "8", "is_sample": False},
        {"input": "9", "expected_output": "9", "is_sample": False}]}
    r = client.put(f"{API}/admin/tests/{tid}/questions/{qid}", headers=admin, json=edited)
    assert r.status_code == 200 and r.json()["data"]["body"] == "v2"
    qs = client.get(f"{API}/admin/tests/{tid}/questions", headers=admin).json()["data"]
    assert len(qs) == 1                                        # no phantom duplicate
    assert [c["input"] for c in qs[0]["test_cases"]] == ["7", "8", "9"] and qs[0]["marks"] == 9
    assert client.get(f"{API}/admin/tests/{tid}/summary", headers=admin).json()["data"]["question_count"] == 1


def test_editing_mcq_and_likert_questions_in_place(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1)
    qid = add_q(client, admin, tid, mcq(body="old"))
    r = client.put(f"{API}/admin/tests/{tid}/questions/{qid}", headers=admin, json=mcq(body="new", opts=("a", "b"), correct=0, marks=3))
    assert r.status_code == 200
    qs = client.get(f"{API}/admin/tests/{tid}/questions", headers=admin).json()["data"]
    assert len(qs) == 1 and qs[0]["body"] == "new" and len(qs[0]["options"]) == 2 and qs[0]["marks"] == 3
    bid = make_test(client, admin, "behaviour")
    lid = add_q(client, admin, bid, {"type": "likert", "body": "s", "options": [{"text": "a", "score": 1}, {"text": "b", "score": 2}]})
    client.put(f"{API}/admin/tests/{bid}/questions/{lid}", headers=admin, json={"type": "likert", "body": "s2", "options": [{"text": "x", "score": 5}, {"text": "y", "score": 6}]})
    got = client.get(f"{API}/admin/tests/{bid}/questions", headers=admin).json()["data"]
    assert len(got) == 1 and got[0]["options"][1]["score"] == 6
