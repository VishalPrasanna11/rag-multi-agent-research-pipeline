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
    mode: RunMode = RunMode.QUESTION
    agents: List[str] = ["pre_earnings", "live_earnings", "transcript", "post_earnings"]
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
    earnings_date: Optional[str] = None
    mode: str
    status: str  # "running" | "review" | "approved" | "final"
    executive_summary: Optional[str] = None
    sections: Dict[str, Optional[AgentResultResponse]] = {}
    synthesis: Optional[str] = None
    charts: List[ChartData] = []
    sources: List[str] = []
    confidence: Optional[float] = None
    created_at: datetime = datetime.now()
    critique_rounds: int = 0


class ApprovalRequest(BaseModel):
    approved: bool
    feedback: Optional[str] = None


class WatchlistItem(BaseModel):
    ticker: str
    next_earnings_date: Optional[str] = None


class StreamEvent(BaseModel):
    event_type: str  # "agent_started" | "agent_done" | "synthesis" | "critique" | "final"
    agent: Optional[str] = None
    status: str
    data: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str
    snowflake: str
    pinecone: str
    redis: str
