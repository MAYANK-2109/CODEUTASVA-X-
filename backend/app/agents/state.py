import operator
from typing import Annotated, TypedDict


def merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class State(TypedDict, total=False):
    query: str
    holdings: list[dict]
    portfolio_source: str
    plan: dict
    # Parallel agents write to these, so each needs a reducer.
    findings: Annotated[dict, merge]
    evidence: Annotated[list[dict], operator.add]
    trace: Annotated[list[dict], operator.add]
    gaps: Annotated[list[str], operator.add]
    answer: dict


def evidence(prefix: str, number: int, claim: str, source: str) -> dict:
    """One traceable fact. IDs are prefixed per agent so parallel agents never collide."""
    return {"id": f"{prefix}{number}", "claim": claim, "source": source}


def inr(amount: float) -> str:
    """Rupees with Indian digit grouping, e.g. ₹4,16,646."""
    sign = "-" if amount < 0 else ""
    digits = str(int(round(abs(amount))))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{sign}₹{digits}"


def pct(fraction: float, signed: bool = True) -> str:
    value = round(fraction * 100, 1)
    if value == 0:
        return "0.0%"  # never "-0.0%"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"
