"""Server-side integrity signals (README §4.3 + §9). These are SIGNALS for an admin to review, never verdicts:
client-side proctoring can be tampered with, so we also look at what the server itself observed."""
import re
from collections import defaultdict
from itertools import combinations

from sqlalchemy.orm import Session

from app.models import Answer, Attempt, AttemptStatus, Candidate, CandidateTest, CodeSubmission, Question, QType

MIN_TOKENS = 30          # tiny solutions ("print(a+b)") are naturally identical; don't flag them
KGRAM = 5
KEYWORDS = {
    "if", "else", "elif", "for", "while", "return", "def", "class", "int", "long", "float", "double", "char", "void",
    "string", "bool", "true", "false", "none", "null", "in", "not", "and", "or", "import", "from", "public", "static",
    "new", "break", "continue", "try", "except", "catch", "switch", "case", "do", "var", "let", "const", "function",
    "lambda", "print", "printf", "cout", "cin", "input", "range", "len", "include", "using", "namespace", "main",
}
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*|#[^\n]*", re.S)
_STRINGS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|&&|\|\||\+\+|--|[^\s\w]")


def normalize(src: str) -> list[str]:
    """Language-agnostic: drop comments, collapse strings/numbers, rename every identifier to ID so that
    renaming variables or reformatting does not hide a copy."""
    src = _STRINGS.sub('"S"', _COMMENTS.sub(" ", src))
    out = []
    for t in _TOKEN.findall(src):
        if t[0].isdigit():
            out.append("N")
        elif t[0].isalpha() or t[0] == "_":
            out.append(t.lower() if t.lower() in KEYWORDS else "ID")
        else:
            out.append(t)
    return out


def fingerprint(src: str) -> tuple[frozenset[int], int]:
    toks = normalize(src)
    grams = {hash(tuple(toks[i:i + KGRAM])) for i in range(max(0, len(toks) - KGRAM + 1))}
    return frozenset(grams), len(toks)


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _people(db: Session, test_id: int) -> dict[int, dict]:
    rows = (db.query(Attempt, Candidate).join(CandidateTest, Attempt.candidate_test_id == CandidateTest.id)
            .join(Candidate, CandidateTest.candidate_id == Candidate.id).filter(CandidateTest.test_id == test_id).all())
    return {a.id: {"attempt": a, "candidate_id": c.id, "name": c.name, "batch": c.batch} for a, c in rows}


def code_similarity(db: Session, test_id: int, threshold: float) -> list[dict]:
    people = _people(db, test_id)
    best: dict[tuple[int, int], CodeSubmission] = {}   # (question, attempt) -> best final submission
    for s in (db.query(CodeSubmission).filter(CodeSubmission.attempt_id.in_(list(people) or [0]),
                                             CodeSubmission.kind == "final").order_by(CodeSubmission.id)):
        k = (s.question_id, s.attempt_id)
        cur = best.get(k)
        ratio = lambda x: x.passed_cases / x.total_cases if x.total_cases else 0
        if cur is None or ratio(s) >= ratio(cur):
            best[k] = s
    by_q: dict[int, list] = defaultdict(list)
    for (qid, aid), s in best.items():
        fp, n = fingerprint(s.source_code)
        if n >= MIN_TOKENS:
            by_q[qid].append((aid, s, fp))
    pairs = []
    for qid, items in by_q.items():
        for (a1, s1, f1), (a2, s2, f2) in combinations(items, 2):
            sim = jaccard(f1, f2)
            if sim >= threshold:
                pairs.append({"question_id": qid, "similarity": round(sim, 3),
                              "a": {"candidate_id": people[a1]["candidate_id"], "name": people[a1]["name"], "submission_id": s1.id, "language": s1.language},
                              "b": {"candidate_id": people[a2]["candidate_id"], "name": people[a2]["name"], "submission_id": s2.id, "language": s2.language}})
    return sorted(pairs, key=lambda p: -p["similarity"])


def answer_concordance(db: Session, test_id: int, min_shared: int = 3, ratio: float = 0.6) -> list[dict]:
    """Two candidates choosing the SAME WRONG option on many MCQs is far more telling than sharing right answers.
    Option order is shuffled per candidate, so a shared wrong choice cannot come from copying screen positions."""
    qs = {q.id: q for q in db.query(Question).filter_by(test_id=test_id, type=QType.mcq.value)}
    people = _people(db, test_id)
    wrong: dict[int, dict[int, int]] = defaultdict(dict)   # attempt -> {question: wrong option}
    for a in db.query(Answer).filter(Answer.attempt_id.in_(list(people) or [0])):
        q = qs.get(a.question_id)
        if q and a.selected_option is not None and a.selected_option != q.correct_option:
            wrong[a.attempt_id][a.question_id] = a.selected_option
    out = []
    for a1, a2 in combinations(list(wrong), 2):
        shared = [q for q, o in wrong[a1].items() if wrong[a2].get(q) == o]
        smaller = min(len(wrong[a1]), len(wrong[a2]))
        if len(shared) >= min_shared and smaller and len(shared) / smaller >= ratio:
            out.append({"shared_wrong": len(shared), "ratio": round(len(shared) / smaller, 2),
                        "a": {"candidate_id": people[a1]["candidate_id"], "name": people[a1]["name"]},
                        "b": {"candidate_id": people[a2]["candidate_id"], "name": people[a2]["name"]}})
    return sorted(out, key=lambda p: -p["shared_wrong"])


def attempt_flags(db: Session, test, ) -> list[dict]:
    """Per-attempt observations from server-side data only."""
    people = _people(db, test.id)
    nq = db.query(Question).filter_by(test_id=test.id).count() or 1
    ips: dict[str, list[int]] = defaultdict(list)
    for aid, p in people.items():
        if p["attempt"].start_ip:
            ips[p["attempt"].start_ip].append(aid)
    flags = []
    for aid, p in people.items():
        a, reasons = p["attempt"], []
        if a.submitted_at and a.score is not None and a.max_score:
            secs = max(1.0, (a.submitted_at - a.started_at).total_seconds())
            allotted = test.duration_min * 60
            if a.submit_reason == "manual" and a.score / a.max_score >= 0.9 and secs < allotted * 0.2:
                reasons.append(f"Scored {round(100 * a.score / a.max_score)}% in {round(secs)} s "
                               f"({round(100 * secs / allotted)}% of the allotted time)")
            if a.submit_reason == "manual" and test.stage != "coding" and secs / nq < 4 and a.score / a.max_score >= 0.6:
                reasons.append(f"Averaged {secs / nq:.1f} s per question")
        if a.violation_count >= max(2, test.max_violations - 1):
            reasons.append(f"{a.violation_count} counted warnings (limit {test.max_violations})")
        if a.status == AttemptStatus.disqualified.value:
            reasons.append("Disqualified by admin")
        if reasons:
            flags.append({"attempt_id": aid, "candidate_id": p["candidate_id"], "name": p["name"], "batch": p["batch"],
                          "score": a.score, "max_score": a.max_score, "reasons": reasons})
    shared_ip = [{"ip": ip, "candidates": [people[i]["name"] for i in ids]} for ip, ids in ips.items() if len(ids) > 1]
    return flags, shared_ip
