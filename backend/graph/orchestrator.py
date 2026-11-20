import logging
import uuid
from typing import Optional, List, AsyncIterator, Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from models.state import EarningsState
from graph.nodes import (
    pre_earnings_node,
    live_earnings_node,
    transcript_node,
    post_earnings_node,
    synthesize_node,
    critique_node,
    refine_node,
    human_review_node,
    finalize_node,
)
from graph.edges import route_by_mode, should_critique, after_refine, after_human_review

logger = logging.getLogger(__name__)


def build_graph() -> CompiledStateGraph:
    workflow = StateGraph(EarningsState)

    workflow.add_node("pre_earnings_node", pre_earnings_node)
    workflow.add_node("live_earnings_node", live_earnings_node)
    workflow.add_node("transcript_node", transcript_node)
    workflow.add_node("post_earnings_node", post_earnings_node)
    workflow.add_node("synthesize", synthesize_node)
    workflow.add_node("critique", critique_node)
    workflow.add_node("refine", refine_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("finalize", finalize_node)

    workflow.set_conditional_entry_point(route_by_mode)

    for node in ["pre_earnings_node", "live_earnings_node", "transcript_node", "post_earnings_node"]:
        workflow.add_edge(node, "synthesize")

    workflow.add_edge("synthesize", "critique")
    workflow.add_conditional_edges("critique", should_critique, {"refine": "refine", "human_review": "human_review"})
    workflow.add_edge("refine", "critique")
    workflow.add_conditional_edges("human_review", after_human_review, {"finalize": "finalize"})
    workflow.add_edge("finalize", END)

    return workflow.compile()


graph = build_graph()


async def run_research(
    ticker: str,
    query: str,
    mode: str = "full_cycle",
    earnings_date: Optional[str] = None,
    active_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    run_id = str(uuid.uuid4())
    initial_state: EarningsState = {
        "ticker": ticker.upper(),
        "earnings_date": earnings_date,
        "query": query,
        "mode": mode,
        "active_agents": active_agents or [],
        "pre_earnings": None,
        "live_earnings": None,
        "transcript_analysis": None,
        "post_earnings": None,
        "synthesis": None,
        "critique_feedback": [],
        "critique_round": 0,
        "final_report": None,
        "human_approved": False,
        "run_id": run_id,
        "error": None,
    }

    logger.info(f"Starting research run {run_id} for {ticker} in mode={mode}")

    try:
        result = await graph.ainvoke(initial_state)
        logger.info(f"Research run {run_id} completed")
        return result
    except Exception as e:
        logger.error(f"Research run {run_id} failed: {e}")
        initial_state["error"] = str(e)
        return initial_state


async def stream_research(
    ticker: str,
    query: str,
    mode: str = "full_cycle",
    earnings_date: Optional[str] = None,
    active_agents: Optional[List[str]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    run_id = str(uuid.uuid4())
    initial_state: EarningsState = {
        "ticker": ticker.upper(),
        "earnings_date": earnings_date,
        "query": query,
        "mode": mode,
        "active_agents": active_agents or [],
        "pre_earnings": None,
        "live_earnings": None,
        "transcript_analysis": None,
        "post_earnings": None,
        "synthesis": None,
        "critique_feedback": [],
        "critique_round": 0,
        "final_report": None,
        "human_approved": False,
        "run_id": run_id,
        "error": None,
    }

    logger.info(f"Starting streaming research run {run_id} for {ticker}")

    async for event in graph.astream(initial_state, stream_mode="updates"):
        for node_name, updates in event.items():
            yield {
                "run_id": run_id,
                "node": node_name,
                "updates": {k: _serialize_result(v) for k, v in updates.items()},
            }


def _serialize_result(val: Any) -> Any:
    if hasattr(val, "response"):
        return {
            "agent_name": val.agent_name,
            "response": val.response,
            "confidence": val.confidence,
            "sources": val.sources,
            "structured_data": val.structured_data,
            "execution_time_ms": val.execution_time_ms,
        }
    return val
