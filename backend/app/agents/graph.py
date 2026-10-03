from collections.abc import Iterator

from langgraph.graph import END, START, StateGraph

from app.agents import nodes
from app.agents.state import State
from app.tools.market import normalise_holdings

PARALLEL = ["sentiment", "weather_macro", "historical"]
AGENTS = ["supervisor", *PARALLEL, "risk", "hedging", "synthesiser"]


def build_graph():
    graph = StateGraph(State)
    for name in AGENTS:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "supervisor")
    for name in PARALLEL:
        graph.add_edge("supervisor", name)
    graph.add_edge(PARALLEL, "risk")
    graph.add_edge("risk", "hedging")
    graph.add_edge("hedging", "synthesiser")
    graph.add_edge("synthesiser", END)
    return graph.compile()


GRAPH = build_graph()


def run(query: str, raw_holdings: list[dict] | None = None) -> Iterator[dict]:
    """Run the pipeline, yielding one event per finished agent and then the answer."""
    holdings, source = normalise_holdings(raw_holdings)
    yield {"type": "start", "agents": AGENTS, "portfolio_source": source, "holdings": len(holdings)}

    skipped = len(raw_holdings or []) - len(holdings) if source == "user" else 0
    gaps = [f"{skipped} holding(s) without a stock symbol and quantity, such as mutual funds, "
            "were left out of the analysis"] if skipped else []
    initial = {"query": query, "holdings": holdings, "portfolio_source": source,
               "findings": {}, "evidence": [], "trace": [], "gaps": gaps}
    for update in GRAPH.stream(initial, stream_mode="updates"):
        for output in update.values():
            for entry in (output or {}).get("trace", []):
                yield {"type": "agent", **entry}
            if output and "answer" in output:
                yield {"type": "answer", **output["answer"]}
