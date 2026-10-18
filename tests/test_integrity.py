from app.services.integrity import fingerprint, jaccard, normalize
from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq

API = "/api/v1"

A = '''def solve(a, b):
    total = 0
    for i in range(a):
        total += i * b
    if total > 100:
        total -= 1
    return total
print(solve(10, 3))  # 42
'''
# same algorithm: renamed variables, different comments/spacing/strings
B = '''# my solution
def compute(x,   y):
    acc=0
    for k in range(x):   # loop
        acc += k*y
    if acc > 100:
        acc -= 1
    return acc
print(compute(10, 3))   # 42 done
'''
C = '''import sys
data = [int(t) for t in sys.stdin.read().split()] or [10, 3]
n, m = data[0], data[1]
res, idx = 0, 0
while idx < n:
    res = res + m * idx
    idx = idx + 1
res = res - 1 if res > 100 else res
print("42" if res else res)
'''


def test_normalize_ignores_names_comments_numbers():
    assert normalize("foo = 1 + 2  # c") == normalize("bar=7+8 /* d */")
    assert jaccard(*[fingerprint(s)[0] for s in (A, B)]) > 0.9
    assert jaccard(*[fingerprint(s)[0] for s in (A, C)]) < 0.5


def coding_world(client, admin):
    tid = make_test(client, admin, "coding", cutoff=1)
    qid = add_q(client, admin, tid, {"type": "coding", "body": "p", "marks": 10, "test_cases": [
        {"input": "", "expected_output": "42", "is_sample": True}, {"input": "", "expected_output": "42", "is_sample": False}]})
    return tid, qid


def submit_code(client, admin, tid, qid, email, src):
    cid = make_candidate(client, admin, email=email)
    assign(client, admin, tid, cid)
    h = login(client, email, "Password123")
    d = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]
    r = client.post(f"{API}/attempts/{d['attempt_id']}/code/submit", headers=h,
                    json={"question_id": qid, "language": "python", "source_code": src})
    assert r.status_code == 200, r.text
    client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)


def test_code_similarity_flags_renamed_copy_but_not_different_solution(client, admin):
    tid, qid = coding_world(client, admin)
    for em, src in (("a@x.com", A), ("b@x.com", B), ("c@x.com", C)):
        submit_code(client, admin, tid, qid, em, src)
    r = client.get(f"{API}/admin/tests/{tid}/integrity", headers=admin).json()["data"]
    assert len(r["code_pairs"]) == 1
    pair = r["code_pairs"][0]
    assert {pair["a"]["name"], pair["b"]["name"]} == {"a", "b"} and pair["similarity"] >= 0.8
    src = client.get(f"{API}/admin/code-submissions/{pair['a']['submission_id']}", headers=admin).json()["data"]["source_code"]
    assert "solve" in src or "compute" in src
    # lowering the threshold surfaces more
    lo = client.get(f"{API}/admin/tests/{tid}/integrity?similarity=0.3", headers=admin).json()["data"]["code_pairs"]
    assert len(lo) >= 1


def test_trivial_identical_solutions_not_flagged(client, admin):
    tid, qid = coding_world(client, admin)
    for em in ("a@x.com", "b@x.com"):
        submit_code(client, admin, tid, qid, em, "print(42)")
    assert client.get(f"{API}/admin/tests/{tid}/integrity", headers=admin).json()["data"]["code_pairs"] == []


def pick(client, h, aid, n, text):
    q = client.get(f"{API}/attempts/{aid}/question?n={n}", headers=h).json()["data"]
    client.put(f"{API}/attempts/{aid}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index(text)})


def test_shared_wrong_answers_flagged_and_shuffle_does_not_hide_it(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1)
    for i in range(5):
        add_q(client, admin, tid, mcq(body=f"Q{i}"))
    picks = {"a@x.com": ["5", "5", "5", "5", "4"], "b@x.com": ["5", "5", "5", "5", "4"], "c@x.com": ["3", "6", "3", "6", "4"]}
    for em, texts in picks.items():
        cid = make_candidate(client, admin, email=em)
        assign(client, admin, tid, cid)
        h = login(client, em, "Password123")
        aid = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]["attempt_id"]
        # question ORDER is shuffled per candidate, so answer by question id, not by position
        for n in range(1, 6):
            q = client.get(f"{API}/attempts/{aid}/question?n={n}", headers=h).json()["data"]
            idx = int(q["body"][1:])
            client.put(f"{API}/attempts/{aid}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index(texts[idx])})
        client.post(f"{API}/attempts/{aid}/submit", headers=h)
    r = client.get(f"{API}/admin/tests/{tid}/integrity", headers=admin).json()["data"]
    assert len(r["answer_pairs"]) == 1
    p = r["answer_pairs"][0]
    assert {p["a"]["name"], p["b"]["name"]} == {"a", "b"} and p["shared_wrong"] == 4


def test_fast_high_score_and_requires_admin(client, admin):
    tid = make_test(client, admin, "aptitude", cutoff=1, duration=30)
    add_q(client, admin, tid, mcq())
    cid = make_candidate(client, admin)
    assign(client, admin, tid, cid)
    h = login(client, "c1@x.com", "Password123")
    aid = client.post(f"{API}/tests/{tid}/start", headers=h).json()["data"]["attempt_id"]
    pick(client, h, aid, 1, "4")
    client.post(f"{API}/attempts/{aid}/submit", headers=h)
    r = client.get(f"{API}/admin/tests/{tid}/integrity", headers=admin).json()["data"]
    assert any("allotted time" in x for f in r["attempt_flags"] for x in f["reasons"])
    assert client.get(f"{API}/admin/tests/{tid}/integrity", headers=h).status_code == 403
    assert client.get(f"{API}/admin/tests/9999/integrity", headers=admin).status_code == 404
