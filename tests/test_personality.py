from tests.conftest import add_q, assign, login, make_candidate, make_test, mcq
from tests.test_flow import answer_all_correct, start

API = "/api/v1"
SCALE = [{"text": t, "score": s} for t, s in [("SD", 1), ("D", 2), ("N", 3), ("A", 4), ("SA", 5)]]
REVERSED = [{"text": o["text"], "score": 6 - o["score"]} for o in SCALE]


def likert(trait, body, options=SCALE):
    return {"type": "likert", "body": body, "options": options, "trait": trait}


def beh_attempt(client, admin, questions, picks, cohort_email="c1@x.com", full=False):
    """Candidate clears aptitude, then answers the behaviour test; picks maps question body -> option text (or None)."""
    apt = make_test(client, admin, "aptitude", cutoff=1)
    beh = make_test(client, admin, "behaviour", cutoff=0)
    add_q(client, admin, apt, mcq())
    for q in questions:
        add_q(client, admin, beh, q)
    cid = make_candidate(client, admin, email=cohort_email)
    assign(client, admin, apt, cid), assign(client, admin, beh, cid)
    h = login(client, cohort_email, "Password123")
    a = start(client, h, apt)["attempt_id"]
    answer_all_correct(client, h, a, 1)
    client.post(f"{API}/attempts/{a}/submit", headers=h)
    d = start(client, h, beh)
    for n in range(1, d["overview"]["total"] + 1):
        q = d["first_question"] if n == 1 else client.get(f"{API}/attempts/{d['attempt_id']}/question?n={n}", headers=h).json()["data"]
        pick = picks.get(q["body"])
        if pick:
            client.put(f"{API}/attempts/{d['attempt_id']}/answers/{q['question_id']}", headers=h, json={"selected": q["options"].index(pick)})
    client.post(f"{API}/attempts/{d['attempt_id']}/submit", headers=h)
    res = client.get(f"{API}/admin/candidates/{cid}/results", headers=admin).json()["data"]
    return res if full else res["stages"][1]["attempt"]["personality"]


def test_trait_is_normalised_and_only_kept_on_likert(client, admin):
    beh = make_test(client, admin, "behaviour")
    add_q(client, admin, beh, likert("  Team   Work ", "S"))
    qs = client.get(f"{API}/admin/tests/{beh}/questions", headers=admin).json()["data"]
    assert qs[0]["trait"] == "team work"
    apt = make_test(client, admin, "aptitude")
    r = client.post(f"{API}/admin/tests/{apt}/questions", headers=admin, json={**mcq(), "trait": "x"})
    assert r.json()["data"]["trait"] is None


def test_csv_import_reads_trait_column(client, admin):
    beh = make_test(client, admin, "behaviour")
    csv = "type,body,options,scores,trait\nlikert,Hi,A|B,1|2,Openness\n"
    r = client.post(f"{API}/admin/tests/{beh}/questions/import", headers=admin, files={"file": ("q.csv", csv, "text/csv")})
    assert r.status_code == 201
    assert client.get(f"{API}/admin/tests/{beh}/questions", headers=admin).json()["data"][0]["trait"] == "openness"


def test_no_traits_means_no_profile(client, admin):
    p = beh_attempt(client, admin, [{"type": "likert", "body": "S", "options": SCALE}], {"S": "A"})
    assert p is None


def test_scores_scale_reverse_items_and_prompts_for_low_trait(client, admin):
    qs = [likert("conscientiousness", "c1"), likert("conscientiousness", "c2", REVERSED),
          likert("teamwork", "t1"), likert("teamwork", "t2")]
    # c1 SA (100) and reverse-scored c2 SD (option score 5 -> 100): fully conscientious. teamwork D/D: 25
    p = beh_attempt(client, admin, qs, {"c1": "SA", "c2": "SD", "t1": "D", "t2": "D"})
    by = {t["trait"]: t for t in p["traits"]}
    assert by["conscientiousness"]["score"] == 100 and by["conscientiousness"]["band"] == "high"
    assert by["teamwork"]["score"] == 25 and by["teamwork"]["band"] == "low"
    assert by["teamwork"]["reading"].startswith("Prefers working alone") and by["conscientiousness"]["reading"].startswith("Organised")
    assert by["teamwork"]["percentile"] is None          # cohort of one: too small to rank
    assert [x["trait"] for x in p["prompts"]] == ["Teamwork"] and p["prompts"][0]["questions"]
    assert p["flags"] == [] and p["reliability"] == "ok"


def test_reliability_flags(client, admin):
    qs = [likert("conscientiousness", f"q{i}") for i in range(8)] + [likert("desirability", f"d{i}") for i in range(3)]
    picks = {f"q{i}": "SA" for i in range(8)} | {f"d{i}": "SA" for i in range(3)}
    p = beh_attempt(client, admin, qs, picks)
    codes = {f["code"] for f in p["flags"]}
    assert {"straight_lining", "desirability", "too_fast"} <= codes
    assert p["reliability"] == "low" and p["desirability"]["score"] == 100
    assert all(t["trait"] != "desirability" for t in p["traits"])      # never part of the profile itself
    assert p["prompts"][-1]["trait"] == "Answer style"


def test_blank_answers_flag_and_unanswered_trait(client, admin):
    qs = [likert("openness", f"o{i}") for i in range(5)]
    p = beh_attempt(client, admin, qs, {"o0": "A"})
    assert "low_completion" in {f["code"] for f in p["flags"]}
    assert p["traits"][0]["answered"] == 1 and p["traits"][0]["score"] == 75


def test_percentile_needs_a_cohort():
    from app.services.personality import _percentile
    assert _percentile(50, [10, 20, 30]) is None                       # 4 people: too few to rank
    assert _percentile(50, [10, 20, 30, 40]) == 100
    assert _percentile(30, [10, 30, 50, 70]) == 38                    # one below + half a tie = 1.5 of 4
