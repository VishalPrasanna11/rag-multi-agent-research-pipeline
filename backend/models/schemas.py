from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class RunMode(str, Enum):
    FULL_CYCLE = "full_cycle"
    PRE_ONLY = "pre_only"
    POST_ONLY = "post_only"
    QUESTION = "question"


class ResearchRequest(BaseModel):
    ticker: str
    query: str
    mode: str = "full_cycle"
    earnings_date: Optional[str] = None
    active_agents: Optional[List[str]] = None
    async_mode: bool = False
    years: Optional[List[int]] = None
    quarters: Optional[List[int]] = None


class AgentResultResponse(BaseModel):
    agent_name: str
    response: str
    confidence: float
    sources: List[str]
    structured_data: Dict[str, Any] = {}
    execution_time_ms: int = 0


class ChartData(BaseModel):
    chart_type: str
    title: str
    data: Optional[str] = None  # base64 PNG or plotly JSON


class EarningsReport(BaseModel):
    run_id: str
    ticker: str
    query: str = ""
    status: str = "running"
    pre_earnings: Optional[AgentResultResponse] = None
    live_earnings: Optional[AgentResultResponse] = None
    transcript_analysis: Optional[AgentResultResponse] = None
    post_earnings: Optional[AgentResultResponse] = None
    synthesis: Optional[AgentResultResponse] = None
    final_report: Optional[str] = None


class ApprovalRequest(BaseModel):
    approved: bool
    feedback: Optional[str] = None


class WatchlistItem(BaseModel):
    ticker: str
    next_earnings: Optional[str] = None


class StreamEvent(BaseModel):
    event_type: str  # "agent_started" | "agent_done" | "synthesis" | "critique" | "final"
    agent: Optional[str] = None
    status: str
    data: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    checks: Dict[str, bool] = {}
