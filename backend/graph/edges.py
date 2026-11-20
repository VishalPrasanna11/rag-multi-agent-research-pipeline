import logging
from typing import List, Literal
from models.state import EarningsState
from config import settings

logger = logging.getLogger(__name__)

RunMode = Literal["full_cycle", "pre_only", "post_only", "question"]


def route_by_mode(state: EarningsState) -> List[str]:
    mode = state.get("mode", "full_cycle")
    active = state.get("active_agents", [])

    if active:
        return active

    if mode == "pre_only":
        return ["pre_earnings_node"]
    elif mode == "post_only":
        return ["post_earnings_node"]
    elif mode == "question":
        return ["pre_earnings_node", "transcript_node"]
    else:
        return ["pre_earnings_node", "live_earnings_node", "transcript_node", "post_earnings_node"]


def should_critique(state: EarningsState) -> str:
    critique_round = state.get("critique_round", 0)
    if critique_round >= settings.MAX_CRITIQUE_ROUNDS:
        logger.info(f"Max critique rounds ({settings.MAX_CRITIQUE_ROUNDS}) reached, skipping")
        return "human_review"

    feedback = state.get("critique_feedback", [])
    high_severity = [fb for fb in feedback if fb.severity == "high"]

    if high_severity and critique_round < settings.MAX_CRITIQUE_ROUNDS:
        logger.info(f"High-severity issues found, refining (round {critique_round + 1})")
        return "refine"

    return "human_review"


def after_refine(state: EarningsState) -> str:
    return "critique"


def after_human_review(state: EarningsState) -> str:
    if state.get("human_approved"):
        return "finalize"
    return "finalize"
