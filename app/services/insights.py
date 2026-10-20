"""Whole-person insights for one candidate: likely team roles, key features, thinking style, work style.

Three kinds of evidence are combined, and every statement carries the evidence it rests on:
  - self-report: the trait scores from the behaviour test (`personality.profile`)
  - behaviour: how the candidate actually worked (aptitude accuracy by difficulty and time used, coding iteration habits)
  - outcome: stage scores
These are hypotheses to test in an interview. Self-report can be faked and a handful of questions cannot describe a person, so
`confidence` says how much each reading rests on.
"""
from sqlalchemy.orm import Session

from app.models import Answer, Attempt, CodeSubmission, Question
from app.services import attempts as svc

STRONG, LIKELY = 70, 58
# team role -> (what they do for a team, where they tend to do well, inputs). Inputs are trait names or "aptitude" / "coding" (stage %)
ROLES = {
    "Driver": ("Pushes the team toward goals and makes the call when it is split.",
               "ownership roles: project lead, client-facing work, anything needing decisions.", ["leadership", "initiative", "extraversion"]),
    "Collaborator": ("Keeps people working together and information flowing.",
                     "team delivery, mentoring, support and cross-team work.", ["teamwork", "agreeableness", "communication"]),
    "Finisher": ("Makes sure work is done properly and on time.",
                 "quality-critical work: testing, operations, review, anything with deadlines.", ["conscientiousness", "integrity"]),
    "Explorer": ("Brings new ideas and adapts when the plan changes.",
                 "prototyping, research, fast-changing requirements.", ["openness", "adaptability"]),
    "Anchor": ("Stays steady and keeps others calm when pressure rises.",
               "high-pressure work: on-call, escalations, tight releases.", ["emotional stability", "resilience"]),
    "Analyst": ("Breaks problems down and checks the logic.",
                "analysis, debugging, algorithmic and data work.", ["aptitude", "coding"]),
}
# short descriptors shown as key-feature chips: (low, high)
FEATURES = {
    "conscientiousness": ("Casual about detail", "Organised and reliable"), "emotional stability": ("Feels pressure strongly", "Calm under pressure"),
    "agreeableness": ("Direct and competitive", "Cooperative"), "extraversion": ("Reserved", "Outgoing"), "openness": ("Prefers the proven", "Curious"),
    "teamwork": ("Independent worker", "Team player"), "leadership": ("Follows more than leads", "Takes charge"),
    "communication": ("Needs help explaining ideas", "Clear communicator"), "adaptability": ("Prefers stable plans", "Adapts quickly"),
    "integrity": ("Flexible with rules", "Honest and accountable"), "initiative": ("Waits for direction", "Self-starter"),
    "resilience": ("Discouraged by setbacks", "Bounces back"),
}


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _aptitude(db: Session, ct) -> dict | None:
    a: Attempt | None = ct.attempt
    if not a or a.status != "submitted":
        return None
    served = svc.attempt_question_ids(db, a)
    qs = {q.id: q for q in db.query(Question).filter(Question.id.in_(served))} if served else {}
    right = {x.question_id: x.is_correct for x in db.query(Answer).filter_by(attempt_id=a.id)}
    by_diff: dict[str, list[int]] = {}
    for qid in served:
        q = qs.get(qid)
        if q and q.type == "mcq":
            by_diff.setdefault(q.difficulty, []).append(1 if right.get(qid) else 0)
    n = sum(len(v) for v in by_diff.values())
    if not n:
        return None
    allowed = ct.test.duration_min * 60
    used = (a.submitted_at - a.started_at).total_seconds() / allowed if a.submitted_at and allowed else None
    return {"accuracy": sum(sum(v) for v in by_diff.values()) / n * 100, "n": n, "used": used,
            "by_diff": {d: (sum(v) / len(v) * 100, len(v)) for d, v in by_diff.items()}}


def _thinking_from_aptitude(ap: dict) -> list[dict]:
    out, acc, used = [], ap["accuracy"], ap["used"]
    ev = f"{acc:.0f}% correct on {ap['n']} questions" + (f", used {used * 100:.0f}% of the time" if used is not None else "")
    if used is not None:
        if acc >= 75 and used <= 0.6:
            out.append(("Quick and accurate", "Gets reasoning questions right without needing much time.", ev))
        elif acc >= 75:
            out.append(("Methodical", "Takes the time to work things through and gets them right.", ev))
        elif acc < 50 and used <= 0.4:
            out.append(("Fast but error-prone", "Finished early with many wrong answers; may rush or skim.", ev))
        elif acc < 50 and used > 0.8:
            out.append(("Struggles with abstract reasoning", "Used most of the time and still missed many; practise or support may help.", ev))
        else:
            out.append(("Mixed reasoning performance", "Accuracy and pace are middling; no clear style stands out.", ev))
    d = ap["by_diff"]
    hard, easy = d.get("hard"), d.get("easy")
    if hard and hard[1] >= 2 and hard[0] >= 60:
        out.append(("Handles harder problems", "Held up on the hard questions, which is where reasoning shows.", f"{hard[0]:.0f}% on {hard[1]} hard questions"))
    if easy and easy[1] >= 2 and easy[0] < 60:
        out.append(("Slips on basics", "Missed many easy questions; this points to carelessness more than inability.", f"{easy[0]:.0f}% on {easy[1]} easy questions"))
    return [{"title": t, "meaning": m, "evidence": e} for t, m, e in out]


def _thinking_from_coding(db: Session, ct) -> list[dict]:
    a = ct.attempt
    if not a:
        return []
    subs = db.query(CodeSubmission).filter_by(attempt_id=a.id).order_by(CodeSubmission.id).all()
    qids = {s.question_id for s in subs}
    if not qids:
        return [{"title": "Did not attempt the coding problems", "meaning": "No code was run or submitted.", "evidence": "0 submissions"}]
    solved = runs = silent_solved = 0
    for qid in qids:
        mine = [s for s in subs if s.question_id == qid]
        r = sum(1 for s in mine if s.kind == "run")
        ok = any(s.kind == "final" and s.total_cases and s.passed_cases == s.total_cases for s in mine)
        runs += r
        solved += ok
        silent_solved += ok and r == 0
    avg_runs = runs / len(qids)
    ev = f"{solved} of {len(qids)} problems fully solved, {avg_runs:.1f} test runs per problem"
    if avg_runs >= 2:
        return [{"title": "Tests as they go", "meaning": "Runs the samples repeatedly and fixes in small steps; a debugging habit that suits real work.", "evidence": ev}]
    if silent_solved and silent_solved == solved and solved:
        return [{"title": "Confident one-shot solver", "meaning": "Solved problems without running the samples first; strong when right, risky when not.", "evidence": ev}]
    if avg_runs < 1 and solved < len(qids):
        return [{"title": "Submits without testing", "meaning": "Little use of the run button before submitting; ask how they check their work.", "evidence": ev}]
    return [{"title": "Steady approach", "meaning": "Some testing before submitting; nothing unusual.", "evidence": ev}]


def _style(t: dict) -> list[dict]:
    """Thinking and work-style readings that need a combination of traits."""
    g = lambda k: t.get(k)
    out = []
    o, c = g("openness"), g("conscientiousness")
    if o is not None and c is not None:
        if o >= LIKELY and c >= LIKELY:
            out.append(("Creative and disciplined", "Generates ideas and also follows them through.", f"openness {o:.0f}%, conscientiousness {c:.0f}%"))
        elif o >= LIKELY > c:
            out.append(("Idea-driven", "Strong on ideas; may need structure and deadlines to land them.", f"openness {o:.0f}%, conscientiousness {c:.0f}%"))
        elif c >= LIKELY > o:
            out.append(("Systematic", "Prefers proven methods and careful steps over experimenting.", f"openness {o:.0f}%, conscientiousness {c:.0f}%"))
        elif o < 45 and c < 45:
            out.append(("Practical, needs direction", "Neither exploratory nor strongly structured; clear instructions help.", f"openness {o:.0f}%, conscientiousness {c:.0f}%"))
    stab = _avg([g("emotional stability"), g("resilience")])
    if stab is not None:
        out.append(("Steady under pressure" if stab >= LIKELY else "Pressure may weigh on them" if stab < 45 else "Average under pressure",
                    "Likely to stay level when deadlines tighten." if stab >= LIKELY else "May need support and clear priorities when things get tense." if stab < 45 else "Neither unusually calm nor unusually affected.",
                    f"stability/resilience {stab:.0f}%"))
    team = _avg([g("teamwork"), g("agreeableness"), g("communication")])
    if team is not None:
        out.append(("Prefers collaborating" if team >= LIKELY else "Prefers working independently" if team < 45 else "Flexible between solo and team",
                    "Does best with regular input from others." if team >= LIKELY else "Likely most productive with clear ownership of their own piece." if team < 45 else "Comfortable either way.",
                    f"teamwork/agreeableness/communication {team:.0f}%"))
    auto = _avg([g("initiative"), g("conscientiousness")])
    if auto is not None and auto >= 65:
        out.append(("Self-directed", "Needs little supervision once the goal is clear.", f"initiative/conscientiousness {auto:.0f}%"))
    elif auto is not None and auto < 45:
        out.append(("Needs guidance", "Works best with regular check-ins and a clear task list.", f"initiative/conscientiousness {auto:.0f}%"))
    return [{"title": a, "meaning": b, "evidence": c_} for a, b, c_ in out]


def build(db: Session, cts: list, personality: dict | None) -> dict | None:
    """`cts`: the candidate's CandidateTest rows (attempt and test loaded). Returns None when there is nothing to say yet."""
    traits = {r["trait"]: r["score"] for r in (personality or {}).get("traits", []) if r["score"] is not None}
    stage = {ct.stage: ct for ct in cts}
    pct = {}
    for k, ct in stage.items():
        a = ct.attempt
        if a and a.status == "submitted" and a.max_score:
            pct[k] = a.score / a.max_score * 100
    ap = _aptitude(db, stage["aptitude"]) if "aptitude" in stage else None
    inputs = {**traits, **{k: v for k, v in pct.items() if k in ("aptitude", "coding")}}
    if not inputs and not ap:
        return None

    roles = []
    for name, (does, env, names) in ROLES.items():
        got = [inputs[n] for n in names if n in inputs]
        if not got:
            continue
        score = sum(got) / len(got)
        roles.append({"role": name, "score": round(score), "does": does, "environment": env,
                      "based_on": [n for n in names if n in inputs], "of": len(names),
                      "fit": "strong" if score >= STRONG else "likely" if score >= LIKELY else "weak" if score < 45 else "partial"})
    roles.sort(key=lambda r: -r["score"])

    features = []
    for k, v in sorted(traits.items(), key=lambda kv: -abs(kv[1] - 50)):
        lo_hi = FEATURES.get(k)
        if lo_hi and (v >= 70 or v < 40):
            features.append({"text": lo_hi[1] if v >= 70 else lo_hi[0], "kind": "strength" if v >= 70 else "watch", "trait": k, "score": round(v)})
    for k, label_ in (("aptitude", "Strong reasoning"), ("coding", "Strong coding")):
        if pct.get(k, 0) >= 80:
            features.append({"text": label_, "kind": "strength", "trait": k, "score": round(pct[k])})

    thinking = (_thinking_from_aptitude(ap) if ap else []) + (_thinking_from_coding(db, stage["coding"]) if "coding" in stage else [])
    work = _style(traits)
    top = [r for r in roles if r["fit"] in ("strong", "likely")][:2]
    suits = [{"role": r["role"], "text": r["environment"]} for r in top]
    # confidence: how many of the pieces of evidence exist at all
    have = sum(1 for x in (bool(traits), ap is not None, "coding" in pct) if x)
    return {"roles": roles, "primary_roles": [r["role"] for r in top], "features": features, "thinking": thinking, "work_style": work,
            "suits": suits, "confidence": "high" if have == 3 and len(traits) >= 5 else "medium" if have >= 2 else "low",
            "evidence": {"traits": len(traits), "aptitude": ap is not None, "coding": "coding" in pct},
            "note": "Readings combine self-report with how the candidate actually worked. Treat them as hypotheses to test in the interview, not conclusions."}
