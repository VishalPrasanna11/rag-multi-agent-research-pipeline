import logging
import os
from typing import Optional, List
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
import plotly.graph_objects as go
from agents.base import BaseAgent
from models.state import AgentResult
from tools.snowflake_client import SnowflakeClient
from tools.market_data import MarketDataClient
from tools.web_search import WebSearchClient
from config import settings

logger = logging.getLogger(__name__)


class PostEarningsAgent(BaseAgent):
    name = "post_earnings"

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.OPENAI_MODEL, temperature=0, api_key=settings.OPENAI_API_KEY)
        self.snowflake = SnowflakeClient()
        self.market = MarketDataClient()
        self.search = WebSearchClient()

    def run(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        earnings_date = kwargs.get("earnings_date")
        flash_report = kwargs.get("flash_report", {})
        sources = []
        context_parts = []
        structured = {}

        if earnings_date:
            try:
                reaction_df = self.market.get_post_earnings_reaction(ticker, earnings_date, days=5)
                if not reaction_df.empty:
                    day0_close = reaction_df.iloc[0]["Close"]
                    day1_close = reaction_df.iloc[1]["Close"] if len(reaction_df) > 1 else day0_close
                    day5_close = reaction_df.iloc[-1]["Close"]

                    structured["day1_return_pct"] = round(((day1_close - day0_close) / day0_close) * 100, 2)
                    structured["day5_return_pct"] = round(((day5_close - day0_close) / day0_close) * 100, 2)
                    structured["gap_direction"] = "up" if structured["day1_return_pct"] > 0 else "down"
                    structured["reaction_pattern"] = self._classify_pattern(structured["day1_return_pct"], structured["day5_return_pct"])

                    context_parts.append(
                        f"Price Reaction:\n"
                        f"  Day 1 return: {structured['day1_return_pct']}%\n"
                        f"  Day 5 return: {structured['day5_return_pct']}%\n"
                        f"  Pattern: {structured['reaction_pattern']}"
                    )
                    sources.append("Yahoo Finance - Price Data")

                    chart_path = self._generate_reaction_chart(reaction_df, ticker, earnings_date)
                    if chart_path:
                        structured["chart_path"] = chart_path
            except Exception as e:
                logger.warning(f"Reaction data error: {e}")

        try:
            revisions = self.search.search_analyst_revisions(ticker)
            if revisions:
                rev_text = "\n".join([f"- {r['title']}" for r in revisions[:5]])
                context_parts.append(f"Analyst Revisions:\n{rev_text}")
                sources.extend([r["url"] for r in revisions[:5]])
        except Exception as e:
            logger.warning(f"Analyst revisions search error: {e}")

        try:
            news = self.search.search_earnings_news(ticker)
            if news:
                news_text = "\n".join([f"- {r['title']}" for r in news[:3]])
                context_parts.append(f"Post-Earnings Coverage:\n{news_text}")
                sources.extend([r["url"] for r in news[:3]])
        except Exception as e:
            logger.warning(f"News search error: {e}")

        if flash_report:
            context_parts.append(f"Flash Report Summary:\n{flash_report.get('response', 'N/A')[:1000]}")

        combined_context = "\n\n".join(context_parts) if context_parts else "Limited post-earnings data available."

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a post-earnings analyst tracking market reaction. Structure your debrief as: "
                "1) Price Reaction Summary (gap, day-1 through day-5 pattern) "
                "2) Reaction Pattern Classification (gap-and-go, gap-and-fade, slow grind, etc.) "
                "3) Analyst Revision Velocity (upgrades/downgrades/target changes in first 5 days) "
                "4) Market Narrative (what story is the market telling about these results?) "
                "5) Comparison to Implied Move (actual vs options-implied) "
                "Be data-driven. Use specific numbers."
            )),
            ("human", "Ticker: {ticker}\nQuery: {query}\n\n{context}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"ticker": ticker, "query": query, "context": combined_context})

        confidence = min(0.8, 0.3 + 0.12 * len(context_parts))

        return AgentResult(
            agent_name=self.name,
            response=response.content,
            confidence=confidence,
            sources=sources,
            structured_data=structured,
        )

    def _classify_pattern(self, day1_return: float, day5_return: float) -> str:
        if day1_return > 2 and day5_return > day1_return:
            return "gap-and-go"
        elif day1_return > 2 and day5_return < day1_return * 0.5:
            return "gap-and-fade"
        elif day1_return < -2 and day5_return < day1_return:
            return "sell-and-keep-selling"
        elif day1_return < -2 and day5_return > day1_return * 0.5:
            return "dip-and-recover"
        elif abs(day1_return) < 2:
            return "muted-reaction"
        return "mixed"

    def _generate_reaction_chart(self, df, ticker: str, earnings_date: str) -> Optional[str]:
        try:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=list(range(len(df))),
                y=df["Close"].tolist(),
                mode="lines+markers",
                name="Close Price",
                line={"color": "#007acc", "width": 2},
            ))
            fig.update_layout(
                title=f"{ticker} Post-Earnings Price Reaction (from {earnings_date})",
                xaxis_title="Trading Days After Earnings",
                yaxis_title="Close Price ($)",
                template="plotly_white",
                height=400,
            )

            os.makedirs(settings.CHARTS_DIR, exist_ok=True)
            chart_path = os.path.join(settings.CHARTS_DIR, f"{ticker}_post_earnings_reaction.html")
            fig.write_html(chart_path)
            return chart_path
        except Exception as e:
            logger.warning(f"Chart generation error: {e}")
            return None
