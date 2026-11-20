import logging
from typing import Dict
from models.state import EarningsState, AgentResult
from agents.pre_earnings import PreEarningsAgent
from agents.live_earnings import LiveEarningsAgent
from agents.transcript import TranscriptAgent
from agents.post_earnings import PostEarningsAgent
from agents.synthesis import SynthesisAgent

logger = logging.getLogger(__name__)

pre_earnings_agent = PreEarningsAgent()
live_earnings_agent = LiveEarningsAgent()
transcript_agent = TranscriptAgent()
post_earnings_agent = PostEarningsAgent()
synthesis_agent = SynthesisAgent()


def pre_earnings_node(state: EarningsState) -> Dict:
    logger.info(f"Running pre_earnings for {state['ticker']}")
    result = pre_earnings_agent.execute(
        query=state["query"],
        ticker=state["ticker"],
    )
    return {"pre_earnings": result}


def live_earnings_node(state: EarningsState) -> Dict:
    logger.info(f"Running live_earnings for {state['ticker']}")
    pre_data = state.get("pre_earnings")
    result = live_earnings_agent.execute(
        query=state["query"],
        ticker=state["ticker"],
        filing_text=state.get("filing_text"),
        filing_path=state.get("filing_path"),
        pre_earnings_data=pre_data.structured_data if pre_data else {},
    )
    return {"live_earnings": result}


def transcript_node(state: EarningsState) -> Dict:
    logger.info(f"Running transcript analysis for {state['ticker']}")
    result = transcript_agent.execute(
        query=state["query"],
        ticker=state["ticker"],
        transcript_text=state.get("transcript_text"),
    )
    return {"transcript_analysis": result}


def post_earnings_node(state: EarningsState) -> Dict:
    logger.info(f"Running post_earnings for {state['ticker']}")
    flash = state.get("live_earnings")
    result = post_earnings_agent.execute(
        query=state["query"],
        ticker=state["ticker"],
        earnings_date=state.get("earnings_date"),
        flash_report={"response": flash.response} if flash else {},
    )
    return {"post_earnings": result}


def synthesize_node(state: EarningsState) -> Dict:
    logger.info("Running synthesis")
    agent_results = {}
    for key in ["pre_earnings", "live_earnings", "transcript_analysis", "post_earnings"]:
        if state.get(key):
            agent_results[key] = state[key]

    result = synthesis_agent.execute(
        query=state["query"],
        ticker=state["ticker"],
        agent_results=agent_results,
    )
    return {"synthesis": result}


def critique_node(state: EarningsState) -> Dict:
    logger.info(f"Running critique (round {state.get('critique_round', 0) + 1})")
    synthesis_result = state.get("synthesis")
    if not synthesis_result:
        return {"critique_feedback": []}

    agent_results = {}
    for key in ["pre_earnings", "live_earnings", "transcript_analysis", "post_earnings"]:
        if state.get(key):
            agent_results[key] = state[key]

    feedback = synthesis_agent.critique(synthesis_result.response, agent_results)
    return {
        "critique_feedback": feedback,
        "critique_round": state.get("critique_round", 0) + 1,
    }


def refine_node(state: EarningsState) -> Dict:
    logger.info("Running refinement based on critique")
    synthesis_result = state.get("synthesis")
    feedback = state.get("critique_feedback", [])

    if not synthesis_result or not feedback:
        return {}

    follow_up_answers = {}
    for fb in feedback:
        agent_key = fb.target_agent
        agent_result = state.get(agent_key)
        if agent_result:
            follow_up_answers[fb.target_agent] = agent_result.response[:300]

    refined = synthesis_agent.refine(synthesis_result.response, feedback, follow_up_answers)

    return {
        "synthesis": AgentResult(
            agent_name="synthesis",
            response=refined,
            confidence=synthesis_result.confidence + 0.05,
            sources=synthesis_result.sources,
            structured_data=synthesis_result.structured_data,
            execution_time_ms=synthesis_result.execution_time_ms,
        )
    }


def human_review_node(state: EarningsState) -> Dict:
    logger.info("Awaiting human review")
    return {"human_approved": False}


def finalize_node(state: EarningsState) -> Dict:
    logger.info("Finalizing report")
    synthesis = state.get("synthesis")
    if not synthesis:
        return {"final_report": "No synthesis available."}

    sections = [synthesis.response]

    for key, label in [
        ("pre_earnings", "Pre-Earnings Brief"),
        ("live_earnings", "Flash Earnings Report"),
        ("transcript_analysis", "Transcript Analysis"),
        ("post_earnings", "Post-Earnings Debrief"),
    ]:
        result = state.get(key)
        if result:
            sections.append(f"\n---\n## {label}\n{result.response}")

    return {
        "final_report": "\n".join(sections),
        "human_approved": True,
    }
