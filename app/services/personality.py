"""Personality / competency profile for a behaviour-stage attempt.

Each Likert question may carry a `trait`. A trait's score is the mean of its answered items, each rescaled to 0-100 between the
lowest and highest option score of THAT question (so reverse-scored items, whose option scores are already reversed, and scales
of different length all line up). Percentiles compare a candidate with everyone else who submitted the same test.

Everything here is a signal for an interviewer, not a verdict: see `FLAG_NOTE`.
"""
from collections import Counter
from statistics import pstdev

from sqlalchemy.orm import Session

from app.models import Answer, Attempt, CandidateTest, Question
from app.models.enums import AttemptStatus
from app.services import attempts as svc

DESIRABILITY = {"desirability", "social desirability", "lie", "lie scale", "impression management"}
MIN_COHORT = 5          # percentiles are meaningless below this
LOW, HIGH = 40, 70      # trait bands (percent of the scale)
FLAG_NOTE = "Flags are reasons to look closer, not proof of faking. Confirm in the interview."

# interviewer follow-ups, shown for traits that score LOW (probe the weakness) and as a fallback for any other trait
PROMPTS = {
    "conscientiousness": ["Tell me about a deadline you nearly missed. What did you do, and what would you change?",
                          "How do you keep track of several tasks at once? Show me what your week looks like."],
    "emotional stability": ["Describe a time things went badly under pressure. How did you react in the moment?",
                            "What do you do when you feel overwhelmed during a project?"],
    "agreeableness": ["Tell me about a disagreement with a teammate. How did you resolve it?",
                      "When have you given way on something you felt strongly about, and why?"],
    "extraversion": ["Describe a time you had to speak up in a group or approach someone you did not know.",
                     "How do you build relationships when you join a new team?"],
    "openness": ["Tell me about something you taught yourself recently. How did you go about it?",
                 "Describe a time you changed your mind after hearing a different approach."],
    "teamwork": ["Describe a team project where it did not go smoothly. What was your part?",
                 "How do you handle a teammate who is not pulling their weight?"],
    "leadership": ["Tell me about a time you guided others without being asked. What happened?",
                   "How do you make a decision when the group is split?"],
    "communication": ["Explain a technical idea you know well as if to someone new. (Ask them to try it now.)",
                      "Tell me about a misunderstanding that cost you time. How did you fix it?"],
    "adaptability": ["Describe a time the plan changed suddenly. What did you do first?",
                     "Tell me about a tool or process you had to learn quickly."],
    "integrity": ["Describe a time you were pressured to cut a corner. What did you do?",
                  "Tell me about a mistake you reported even though nobody would have noticed."],
    "initiative": ["Give an example of something you started without being told to.",
                   "Tell me about a problem you noticed and fixed that was not your job."],
    "resilience": ["Describe your biggest setback in the last two years and how you came back from it.",
                   "Tell me about feedback that was hard to hear. What did you do with it?"],
}
# what a low / high score tends to look like at work: shown beside the score so the number is readable by a non-specialist
READING = {
    "conscientiousness": ("May be casual about deadlines and detail; needs clear structure and follow-up.",
                          "Organised, reliable and thorough; likely to finish what they start."),
    "emotional stability": ("May feel pressure strongly and be slower to recover from setbacks.",
                            "Calm under pressure and steady after setbacks."),
    "agreeableness": ("Direct and independent; may clash or compete more than cooperate.",
                      "Cooperative, considerate and easy to work with; may avoid conflict."),
    "extraversion": ("Reserved and reflective; may need encouragement to speak up.",
                     "Energetic and outgoing; comfortable leading conversations."),
    "openness": ("Prefers the familiar and proven; may resist new methods.",
                 "Curious and quick to try new ideas and tools."),
    "teamwork": ("Prefers working alone; may under-share or under-collaborate.",
                 "Works well with others and shares credit and information."),
    "leadership": ("Tends to follow rather than direct others.",
                   "Takes charge, steers decisions and motivates others."),
    "communication": ("May struggle to explain ideas or listen actively.",
                      "Explains clearly and listens well."),
    "adaptability": ("May find changes of plan unsettling.",
                     "Adjusts quickly when plans or tools change."),
    "integrity": ("Answers suggest some flexibility with rules; verify in references.",
                  "Strong sense of honesty and ownership of mistakes."),
    "initiative": ("Tends to wait for direction.", "Acts without being asked and spots problems early."),
    "resilience": ("May be discouraged by setbacks or criticism.", "Bounces back from setbacks and uses feedback."),
}
GENERIC = ["Ask for a specific example from a project or study group that shows this trait in action.",
           "Ask what they would do differently next time in that example."]
ALIASES = {"conscientious": "conscientiousness", "stability": "emotional stability", "neuroticism": "emotional stability",
           "emotional_stability": "emotional stability", "extroversion": "extraversion", "agreeable": "agreeableness",
           "team work": "teamwork", "collaboration": "teamwork", "resilient": "resilience", "ownership": "initiative"}


def canon(trait: str) -> str:
    t = " ".join(trait.lower().replace("_", " ").split())
    return ALIASES.get(t, t)


def label(trait: str) -> str:
    return trait.title()


def _item_pct(q: Question, selected: int | None) -> float | None:
    """0-100 position of the chosen option on this question's own scale; None if unanswered or degenerate."""
    if selected is None or not q.options_json or not 0 <= selected < len(q.options_json):
        return None
    scores = [o.get("score") for o in q.options_json]
    if any(s is None for s in scores) or max(scores) == min(scores):
        return None
    return (scores[selected] - min(scores)) / (max(scores) - min(scores)) * 100


def _trait_scores(questions: list[Question], selected: dict[int, int | None]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for q in questions:
        if not q.trait:
            continue
        t = out.setdefault(canon(q.trait), {"items": [], "n": 0})
        t["n"] += 1
        p = _item_pct(q, selected.get(q.id))
        if p is not None:
            t["items"].append(p)
    for t in out.values():
        t["pct"] = round(sum(t["items"]) / len(t["items"]), 1) if t["items"] else None
    return out


def _cohort(db: Session, test_id: int, exclude_attempt: int, questions: list[Question]) -> dict[str, list[float]]:
    """Trait scores of every OTHER submitted attempt of this test."""
    rows = (db.query(Answer.attempt_id, Answer.question_id, Answer.selected_option)
            .join(Attempt, Attempt.id == Answer.attempt_id).join(CandidateTest, CandidateTest.id == Attempt.candidate_test_id)
            .filter(CandidateTest.test_id == test_id, Attempt.status == AttemptStatus.submitted.value,
                    Attempt.id != exclude_attempt).all())
    by_attempt: dict[int, dict[int, int | None]] = {}
    for aid, qid, sel in rows:
        by_attempt.setdefault(aid, {})[qid] = sel
    cohort: dict[str, list[float]] = {}
    for sel in by_attempt.values():
        for trait, t in _trait_scores(questions, sel).items():
            if t["pct"] is not None:
                cohort.setdefault(trait, []).append(t["pct"])
    return cohort


def _percentile(value: float, others: list[float]) -> int | None:
    if len(others) + 1 < MIN_COHORT:
        return None
    below = sum(1 for x in others if x < value) + 0.5 * sum(1 for x in others if x == value)
    return round(below / len(others) * 100)


def profile(db: Session, attempt: Attempt, test_id: int) -> dict | None:
    """Trait profile, reliability flags and interview prompts for one behaviour attempt, or None if no question has a trait."""
    served = set(svc.attempt_question_ids(db, attempt))
    questions = [q for q in db.query(Question).filter_by(test_id=test_id).order_by(Question.id)
                 if q.id in served and q.type == "likert"]
    if not any(q.trait for q in questions):
        return None
    selected = {a.question_id: a.selected_option for a in db.query(Answer).filter_by(attempt_id=attempt.id)}
    answered = [q for q in questions if _item_pct(q, selected.get(q.id)) is not None]
    scores = _trait_scores(questions, selected)
    cohort = _cohort(db, test_id, attempt.id, questions)

    traits, desir = [], None
    for key, t in sorted(scores.items()):
        row = {"trait": key, "label": label(key), "items": t["n"], "answered": len(t["items"]), "score": t["pct"],
               "percentile": _percentile(t["pct"], cohort.get(key, [])) if t["pct"] is not None else None,
               "band": None if t["pct"] is None else "low" if t["pct"] < LOW else "high" if t["pct"] >= HIGH else "mid"}
        lo_hi = READING.get(key)
        row["reading"] = (lo_hi[0] if row["band"] == "low" else lo_hi[1] if row["band"] == "high" else None) if lo_hi else None
        if key in DESIRABILITY:
            desir = row
        else:
            traits.append(row)

    flags = []
    if len(answered) >= 8:
        _, top = Counter(selected[q.id] for q in answered).most_common(1)[0]
        if top / len(answered) >= 0.9:
            flags.append({"code": "straight_lining", "label": "Same answer position almost every time",
                          "detail": f"{top} of {len(answered)} answers used the same option position."})
    if attempt.submitted_at and len(answered) >= 5:
        per = (attempt.submitted_at - attempt.started_at).total_seconds() / len(answered)
        if per < 2:
            flags.append({"code": "too_fast", "label": "Answered very quickly",
                          "detail": f"About {per:.1f} s per question; reading alone usually takes longer."})
    if questions and len(answered) / len(questions) < 0.8:
        flags.append({"code": "low_completion", "label": "Many questions left blank",
                      "detail": f"{len(answered)} of {len(questions)} answered, so trait scores rest on fewer items."})
    for key, t in scores.items():
        if key not in DESIRABILITY and len(t["items"]) >= 4 and pstdev(t["items"]) > 35:
            flags.append({"code": "inconsistent", "label": f"Mixed answers on {label(key)}",
                          "detail": "Statements meant to agree with each other were answered very differently."})
    if desir and desir["score"] is not None and desir["answered"] >= 3 and desir["score"] >= 85:
        flags.append({"code": "desirability", "label": "Possible answering to look good",
                      "detail": f"Scored {desir['score']:.0f}% on statements almost nobody can honestly agree with."})

    prompts = []
    for r in sorted((r for r in traits if r["band"] == "low"), key=lambda r: r["score"]):
        prompts.append({"trait": r["label"], "reason": f"Scored {r['score']:.0f}% (below {LOW}%)",
                        "questions": PROMPTS.get(r["trait"], GENERIC)})
    if flags:
        prompts.append({"trait": "Answer style", "reason": "; ".join(f["label"] for f in flags),
                        "questions": ["Walk me through how you approached the questionnaire.",
                                      "Pick one statement you answered strongly and give me a real example."]})
    return {"traits": traits, "desirability": desir, "flags": flags, "reliability": "ok" if not flags else "caution" if len(flags) == 1 else "low",
            "prompts": prompts, "cohort_size": max((len(v) for v in cohort.values()), default=0) + 1, "min_cohort": MIN_COHORT,
            "note": FLAG_NOTE}
