"""Per-test analytics: outcome, score distribution, timing, per-question item analysis, college/batch breakdown.
Everything is computed from stored attempts; questions are judged only against the candidates who were SERVED them
(question pools mean different candidates see different questions)."""
from collections import Counter, defaultdict
from statistics import mean, median

from sqlalchemy.orm import Session

from app.models import Answer, Attempt, AttemptStatus, Candidate, CandidateTest, CodeSubmission, College, Question, QType, Test
from app.services.attempts import attempt_question_ids

MIN_SERVED_FOR_FLAGS = 5       # don't judge a question on fewer candidates than this
EASY, HARD = 0.95, 0.20


def _pct(a: Attempt) -> float | None:
    return round(100 * a.score / a.max_score, 1) if a.score is not None and a.max_score else None


def test_analytics(db: Session, test: Test) -> dict:
    rows = (db.query(Attempt, Candidate).join(CandidateTest, Attempt.candidate_test_id == CandidateTest.id)
            .join(Candidate, CandidateTest.candidate_id == Candidate.id).filter(CandidateTest.test_id == test.id).all())
    done = [(a, c) for a, c in rows if a.status != AttemptStatus.active.value and a.score is not None]
    active = len(rows) - len(done)
    assigned = db.query(CandidateTest).filter_by(test_id=test.id).count()
    out = {"test": {"id": test.id, "title": test.title, "stage": test.stage, "cutoff": test.cutoff_score, "max_violations": test.max_violations},
           "assigned": assigned, "started": len(rows), "completed": len(done), "in_progress": active, "not_started": assigned - len(rows)}
    if not done:
        out.update(empty=True)
        return out

    pcts = [p for p in (_pct(a) for a, _ in done) if p is not None]
    passed = [a for a, _ in done if a.passed]
    mins = [(a.submitted_at - a.started_at).total_seconds() / 60 for a, _ in done if a.submitted_at]
    bins = [0] * 10
    below = [0] * 10   # how many in each bin are under the cutoff (so the chart can colour them differently)
    for a, _ in done:
        p = _pct(a)
        if p is None:
            continue
        b = min(int(p // 10), 9)
        bins[b] += 1
        if not a.passed:
            below[b] += 1
    out.update(empty=False, pass_rate=round(100 * len(passed) / len(done), 1), avg_percent=round(mean(pcts), 1) if pcts else None,
               median_percent=round(median(pcts), 1) if pcts else None, top_percent=max(pcts) if pcts else None,
               avg_minutes=round(mean(mins), 1) if mins else None, median_minutes=round(median(mins), 1) if mins else None,
               allotted_minutes=test.duration_min, histogram={"bins": bins, "below_cutoff": below},
               auto_submitted=sum(1 for a, _ in done if a.submit_reason == "violation_limit"),
               timed_out=sum(1 for a, _ in done if a.submit_reason == "timeout"),
               disqualified=sum(1 for a, _ in done if a.status == AttemptStatus.disqualified.value),
               avg_violations=round(mean(a.violation_count for a, _ in done), 2), with_violations=sum(1 for a, _ in done if a.violation_count))

    # ---- item analysis
    qs = {q.id: q for q in db.query(Question).filter_by(test_id=test.id)}
    attempt_ids = [a.id for a, _ in done]
    answers: dict[int, dict[int, Answer]] = defaultdict(dict)
    for ans in db.query(Answer).filter(Answer.attempt_id.in_(attempt_ids)):
        answers[ans.question_id][ans.attempt_id] = ans
    best_code: dict[tuple[int, int], float] = {}
    for s in db.query(CodeSubmission).filter(CodeSubmission.attempt_id.in_(attempt_ids), CodeSubmission.kind == "final"):
        if s.total_cases:
            k = (s.question_id, s.attempt_id)
            best_code[k] = max(best_code.get(k, 0.0), s.passed_cases / s.total_cases)
    served: dict[int, list[int]] = defaultdict(list)
    for a, _ in done:
        for qid in attempt_question_ids(db, a):
            if qid in qs:
                served[qid].append(a.id)

    items = []
    for qid, q in sorted(qs.items()):
        sv = served.get(qid, [])
        item = {"id": qid, "type": q.type, "body": q.body if len(q.body) <= 140 else q.body[:140] + "…", "difficulty": q.difficulty, "served": len(sv), "flags": []}
        if q.type == QType.mcq.value:
            given = [answers[qid][i] for i in sv if i in answers[qid] and answers[qid][i].selected_option is not None]
            cnt = Counter(g.selected_option for g in given)
            correct = cnt.get(q.correct_option, 0)
            item.update(answered=len(given), correct_rate=round(correct / len(sv), 3) if sv else None, skipped=len(sv) - len(given),
                        options=[{"text": o["text"], "chosen": cnt.get(i, 0), "correct": i == q.correct_option} for i, o in enumerate(q.options_json)])
            if len(sv) >= MIN_SERVED_FOR_FLAGS:
                rate = correct / len(sv)
                top_wrong = max(((i, n) for i, n in cnt.items() if i != q.correct_option), key=lambda x: x[1], default=None)
                if rate >= EASY:
                    item["flags"].append(["easy", "Almost everyone got this right; it does not separate candidates."])
                if rate <= HARD:
                    item["flags"].append(["hard", "Very few got this right. Check it is fair and the answer key is correct."])
                if top_wrong and top_wrong[1] > correct and top_wrong[1] >= 3:
                    item["flags"].append(["key", f"The most chosen option is wrong ('{q.options_json[top_wrong[0]]['text'][:40]}'). Re-check the answer key."])
        elif q.type == QType.likert.value:
            given = [answers[qid][i] for i in sv if i in answers[qid] and answers[qid][i].selected_option is not None]
            cnt = Counter(g.selected_option for g in given)
            scores = [float(q.options_json[g.selected_option]["score"]) for g in given]
            item.update(answered=len(given), avg_score=round(mean(scores), 2) if scores else None, max_score=max(o["score"] for o in q.options_json),
                        options=[{"text": o["text"], "chosen": cnt.get(i, 0), "score": o["score"]} for i, o in enumerate(q.options_json)])
            if len(given) >= MIN_SERVED_FOR_FLAGS and len(cnt) == 1:
                item["flags"].append(["flat", "Everyone chose the same option; this statement does not discriminate."])
        else:
            ratios = [best_code.get((qid, i), 0.0) for i in sv]
            attempted = sum(1 for i in sv if (qid, i) in best_code)
            item.update(attempted=attempted, avg_ratio=round(mean(ratios), 3) if ratios else None, solved=sum(1 for r in ratios if r >= 1.0))
            if len(sv) >= MIN_SERVED_FOR_FLAGS:
                if ratios and mean(ratios) <= HARD:
                    item["flags"].append(["hard", "Very few candidates passed most test cases. Check the statement and the hidden cases."])
                if sv and attempted / len(sv) < 0.5:
                    item["flags"].append(["skipped", "More than half of the candidates never submitted code for this."])
        items.append(item)
    out["items"] = items

    # ---- breakdown by college and batch (only where there is data)
    college_name = {c.id: c.name for c in db.query(College)}
    def group(keyfn):
        g: dict[str, list] = defaultdict(list)
        for a, c in done:
            g[keyfn(c)].append(a)
        return sorted(({"name": k or "—", "completed": len(v), "pass_rate": round(100 * sum(1 for a in v if a.passed) / len(v), 1),
                        "avg_percent": round(mean(p for p in (_pct(a) for a in v) if p is not None), 1) if any(_pct(a) is not None for a in v) else None}
                       for k, v in g.items()), key=lambda r: -r["completed"])
    out["by_college"] = group(lambda c: college_name.get(c.college_id, ""))
    out["by_batch"] = group(lambda c: c.batch)
    return out
