from tests.test_personality import SCALE, beh_attempt, likert

API = "/api/v1"


def insights_for(client, admin, qs, picks):
    return beh_attempt(client, admin, qs, picks, full=True)["insights"]


def test_roles_features_and_work_style_from_traits(client, admin):
    qs = [likert("teamwork", "t1"), likert("agreeableness", "a1"), likert("communication", "m1"),
          likert("conscientiousness", "c1"), likert("openness", "o1"), likert("emotional stability", "e1")]
    picks = {"t1": "SA", "a1": "SA", "m1": "A", "c1": "SA", "o1": "SD", "e1": "SD"}
    ins = insights_for(client, admin, qs, picks)
    roles = {r["role"]: r for r in ins["roles"]}
    assert roles["Collaborator"]["score"] == 92 and roles["Collaborator"]["fit"] == "strong" and roles["Collaborator"]["based_on"] == ["teamwork", "agreeableness", "communication"]
    assert roles["Explorer"]["fit"] == "weak" and "Driver" not in roles           # no leadership/initiative/extraversion answered
    assert ins["primary_roles"][0] in ("Collaborator", "Finisher")
    texts = {f["text"]: f["kind"] for f in ins["features"]}
    assert texts["Team player"] == "strength" and texts["Prefers the proven"] == "watch" and texts["Feels pressure strongly"] == "watch"
    titles = {w["title"] for w in ins["work_style"]}
    assert "Systematic" in titles and "Pressure may weigh on them" in titles and "Prefers collaborating" in titles
    assert ins["evidence"]["traits"] == 6 and ins["confidence"] in ("medium", "high")


def test_thinking_style_from_aptitude_behaviour(client, admin):
    ins = insights_for(client, admin, [likert("openness", "o1")], {"o1": "A"})
    t = {x["title"] for x in ins["thinking"]}
    assert "Quick and accurate" in t                     # 1 of 1 correct, submitted almost immediately
    assert "coding" not in ins["evidence"] or ins["evidence"]["coding"] is False


def test_no_traits_still_gives_aptitude_reading(client, admin):
    ins = insights_for(client, admin, [{"type": "likert", "body": "S", "options": SCALE}], {"S": "A"})
    assert ins["evidence"]["traits"] == 0 and ins["evidence"]["aptitude"] is True
    assert [r["role"] for r in ins["roles"]] == ["Analyst"]
