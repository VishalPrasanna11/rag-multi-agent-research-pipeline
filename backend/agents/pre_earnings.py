import logging
from typing import Optional, List
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from agents.base import BaseAgent
from models.state import AgentResult
from tools.snowflake_client import SnowflakeClient
from tools.market_data import MarketDataClient
from tools.web_search import WebSearchClient
from tools.fmp_client import FMPClient
from config import settings

logger = logging.getLogger(__name__)


class PreEarningsAgent(BaseAgent):
    name = "pre_earnings"

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.OPENAI_MODEL, temperature=0, api_key=settings.OPENAI_API_KEY)
        self.snowflake = SnowflakeClient()
        self.market = MarketDataClient()
        self.search = WebSearchClient()
        self.fmp = FMPClient()

    def run(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        context_parts = []
        sources = []
        structured = {}

        try:
            estimates = self.fmp.get_analyst_estimates(ticker, limit=8)
            if estimates:
                latest = estimates[0]
                structured["consensus_eps"] = latest.get("estimatedEpsAvg")
                structured["consensus_revenue"] = latest.get("estimatedRevenueAvg")
                context_parts.append(f"Consensus Estimates:\n{self._format_estimates(estimates[:4])}")
                sources.append("Financial Modeling Prep - Analyst Estimates")
        except Exception as e:
            logger.warning(f"FMP estimates error: {e}")

        try:
            surprises = self.fmp.get_earnings_surprises(ticker)
            if surprises:
                beat_count = sum(1 for s in surprises[:8] if s.get("actualEarningResult", "") == "Beat")
                structured["historical_beat_rate"] = beat_count / min(len(surprises), 8)
                context_parts.append(f"Historical Beat Rate: {beat_count}/{min(len(surprises), 8)} quarters")
                sources.append("Financial Modeling Prep - Earnings Surprises")
        except Exception as e:
            logger.warning(f"FMP surprises error: {e}")

        try:
            options = self.market.get_options_chain(ticker)
            if options.get("implied_move_pct"):
                structured["implied_move_pct"] = options["implied_move_pct"]
                structured["avg_iv"] = options.get("avg_iv")
                context_parts.append(f"Options-Implied Move: {options['implied_move_pct']}% (IV: {options.get('avg_iv')}%)")
                sources.append("Yahoo Finance - Options Chain")
        except Exception as e:
            logger.warning(f"Options chain error: {e}")

        try:
            supply_results = self.search.search_supply_chain(ticker)
            if supply_results:
                supply_text = "\n".join([f"- {r['title']}" for r in supply_results[:3]])
                context_parts.append(f"Supply Chain Signals:\n{supply_text}")
                sources.extend([r["url"] for r in supply_results[:3]])
        except Exception as e:
            logger.warning(f"Supply chain search error: {e}")

        try:
            news = self.search.search_earnings_news(ticker)
            if news:
                news_text = "\n".join([f"- {r['title']}" for r in news[:3]])
                context_parts.append(f"Recent News:\n{news_text}")
                sources.extend([r["url"] for r in news[:3]])
        except Exception as e:
            logger.warning(f"News search error: {e}")

        combined_context = "\n\n".join(context_parts) if context_parts else "Limited data available."

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a senior equity research analyst preparing a pre-earnings brief. "
                "Synthesize the available data into a clear, actionable brief covering: "
                "1) Consensus expectations (EPS, revenue) "
                "2) Historical beat/miss pattern "
                "3) Options-implied move "
                "4) Supply chain signals "
                "5) Key metrics to watch this quarter. "
                "Be specific with numbers. Flag any data that was unavailable."
            )),
            ("human", "Ticker: {ticker}\nQuery: {query}\n\nAvailable Data:\n{context}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"ticker": ticker, "query": query, "context": combined_context})

        confidence = min(0.9, 0.3 + 0.15 * len(context_parts))

        return AgentResult(
            agent_name=self.name,
            response=response.content,
            confidence=confidence,
            sources=sources,
            structured_data=structured,
        )

    def _format_estimates(self, estimates: list) -> str:
        lines = []
        for est in estimates:
            period = f"FY{est.get('date', 'N/A')[:4]} Q{est.get('date', 'N/A')[5:7]}"
            lines.append(f"  {period}: EPS est {est.get('estimatedEpsAvg', 'N/A')}, Revenue est ${est.get('estimatedRevenueAvg', 'N/A'):,.0f}")
        return "\n".join(lines)
