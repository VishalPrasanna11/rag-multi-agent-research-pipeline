from typing import TypedDict, Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentResult:
    agent_name: str
    response: str
    confidence: float
    sources: List[str]
    structured_data: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: int = 0


@dataclass
class CritiqueFeedback:
    target_agent: str
    question: str
    severity: str  # "high", "medium", "low"
    contradiction: Optional[str] = None


class EarningsState(TypedDict):
    ticker: str
    earnings_date: str
    query: str
    mode: str  # "full_cycle" | "pre_only" | "post_only" | "question"
    active_agents: List[str]
    pre_earnings: Optional[AgentResult]
    live_earnings: Optional[AgentResult]
    transcript_analysis: Optional[AgentResult]
    post_earnings: Optional[AgentResult]
    synthesis: Optional[str]
    critique_feedback: Optional[List[CritiqueFeedback]]
    critique_round: int
    final_report: Optional[str]
    human_approved: bool
    run_id: str
    error: Optional[str]
