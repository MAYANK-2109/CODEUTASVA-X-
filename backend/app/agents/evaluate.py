"""Measures how accurately questions are routed to event types, on a labelled set.

A route is correct only when it names exactly the event types the question asks
about. Both routers are measured: the keyword rules, and the language model when
a key is configured.

Run with:  uv run python -m app.agents.evaluate
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

from app.agents import llm, nodes

DATA = Path(__file__).resolve().parent.parent.parent / "data"
QUESTIONS_FILE = DATA / "eval" / "routing_questions.json"
OUT_FILE = DATA / "trained" / "routing_eval.json"
LLM_GAP_SECONDS = 4.5   # the free tier allows about 15 calls a minute


def score(questions: list[dict], route) -> dict:
    """Exact-match accuracy overall and by kind of question, with the misses listed."""
    by_kind: dict[str, list[bool]] = {}
    misses, unanswered = [], 0
    for row in questions:
        got = route(row["question"])
        if got is None:
            unanswered += 1
            continue
        right = set(got) == set(row["expected"])
        by_kind.setdefault(row["kind"], []).append(right)
        if not right:
            misses.append({"question": row["question"], "expected": row["expected"], "got": got})
    answered = sum(len(v) for v in by_kind.values())
    correct = sum(sum(v) for v in by_kind.values())
    return {
        "questions": answered, "correct": correct,
        "accuracy": round(correct / answered, 3) if answered else None,
        "unanswered": unanswered,
        "by_kind": {kind: {"questions": len(v), "correct": sum(v)} for kind, v in by_kind.items()},
        "misses": misses,
    }


def _llm_route(question: str) -> list[str] | None:
    time.sleep(LLM_GAP_SECONDS)
    plan = nodes._llm_plan(question)
    return None if plan is None else plan["event_types"]


def evaluate() -> dict:
    questions = json.loads(QUESTIONS_FILE.read_text())["questions"]
    out = {
        "evaluated_on": date.today().isoformat(),
        "questions": len(questions),
        "rule": "Correct only if the route names exactly the event types the question asks about",
        "rules": score(questions, nodes.classify_events),
        "llm": None,
    }
    if llm.available():
        out["llm"] = {**score(questions, _llm_route), "model": llm.status().get("model")}
    OUT_FILE.write_text(json.dumps(out, indent=1))
    return out


def load() -> dict | None:
    try:
        return json.loads(OUT_FILE.read_text())
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    result = evaluate()
    for name in ("rules", "llm"):
        r = result[name]
        if r:
            print(f'{name}: {r["correct"]} of {r["questions"]} correct ({r["accuracy"]:.0%}), '
                  f'{r["unanswered"]} unanswered, by kind {r["by_kind"]}', file=sys.stderr)
            for miss in r["misses"]:
                print(f'   expected {miss["expected"]} got {miss["got"]}: {miss["question"]}', file=sys.stderr)
