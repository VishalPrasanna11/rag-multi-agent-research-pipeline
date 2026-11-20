import logging
from typing import Optional, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from agents.base import BaseAgent
from models.state import AgentResult
from tools.pdf_parser import PDFParser
from tools.snowflake_client import SnowflakeClient
from config import settings

logger = logging.getLogger(__name__)


class LiveEarningsAgent(BaseAgent):
    name = "live_earnings"

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.OPENAI_MODEL, temperature=0, api_key=settings.OPENAI_API_KEY)
        self.pdf_parser = PDFParser()
        self.snowflake = SnowflakeClient()

    def run(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        filing_text = kwargs.get("filing_text")
        filing_path = kwargs.get("filing_path")
        pre_earnings_data = kwargs.get("pre_earnings_data", {})

        metrics = {}
        full_text = ""

        if filing_path:
            parsed = self.pdf_parser.parse_earnings_release(filing_path)
            metrics = parsed["metrics"]
            full_text = parsed["full_text"]
        elif filing_text:
            full_text = filing_text
            metrics = self.pdf_parser.extract_earnings_metrics(filing_text)

        surprises = self._compute_surprises(metrics, pre_earnings_data)

        try:
            if metrics:
                fiscal_year = years[0] if years else None
                fiscal_quarter = quarters[0] if quarters else None
                if fiscal_year and fiscal_quarter:
                    if "eps" in metrics:
                        self.snowflake.write_actuals(ticker, fiscal_year, fiscal_quarter, "EPS", metrics["eps"])
                    if "revenue_millions" in metrics:
                        self.snowflake.write_actuals(ticker, fiscal_year, fiscal_quarter, "Revenue", metrics["revenue_millions"])
        except Exception as e:
            logger.warning(f"Failed to write actuals to Snowflake: {e}")

        context = f"Extracted Metrics:\n{self._format_metrics(metrics)}"
        if surprises:
            context += f"\n\nSurprises vs Consensus:\n{self._format_surprises(surprises)}"
        if full_text:
            context += f"\n\nRelease Text (first 3000 chars):\n{full_text[:3000]}"

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a financial analyst producing a flash earnings report. "
                "Compare reported actuals to consensus expectations. "
                "Flag any surprise > 2% as significant. "
                "Structure: 1) Headline numbers 2) Beat/miss summary 3) Guidance highlights 4) Key takeaways. "
                "Be precise with numbers and percentages."
            )),
            ("human", "Ticker: {ticker}\nQuery: {query}\n\n{context}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"ticker": ticker, "query": query, "context": context})

        confidence = 0.85 if metrics else 0.4

        return AgentResult(
            agent_name=self.name,
            response=response.content,
            confidence=confidence,
            sources=["SEC 8-K Filing", "Snowflake - Consensus Estimates"],
            structured_data={"metrics": metrics, "surprises": surprises},
        )

    def _compute_surprises(self, actuals: Dict[str, Any], expectations: Dict[str, Any]) -> Dict[str, Any]:
        surprises = {}
        if "eps" in actuals and "consensus_eps" in expectations and expectations["consensus_eps"]:
            actual_eps = actuals["eps"]
            expected_eps = expectations["consensus_eps"]
            surprise_pct = ((actual_eps - expected_eps) / abs(expected_eps)) * 100
            surprises["eps"] = {
                "actual": actual_eps,
                "expected": expected_eps,
                "surprise_pct": round(surprise_pct, 2),
                "beat": surprise_pct > 0,
            }
        if "revenue_millions" in actuals and "consensus_revenue" in expectations and expectations["consensus_revenue"]:
            actual_rev = actuals["revenue_millions"]
            expected_rev = expectations["consensus_revenue"] / 1_000_000
            surprise_pct = ((actual_rev - expected_rev) / abs(expected_rev)) * 100
            surprises["revenue"] = {
                "actual": actual_rev,
                "expected": expected_rev,
                "surprise_pct": round(surprise_pct, 2),
                "beat": surprise_pct > 0,
            }
        return surprises

    def _format_metrics(self, metrics: Dict[str, Any]) -> str:
        if not metrics:
            return "No metrics extracted from filing."
        lines = []
        for k, v in metrics.items():
            if k != "guidance_text":
                lines.append(f"  {k}: {v}")
        if "guidance_text" in metrics:
            lines.append(f"  Guidance: {metrics['guidance_text'][:200]}")
        return "\n".join(lines)

    def _format_surprises(self, surprises: Dict[str, Any]) -> str:
        lines = []
        for metric, data in surprises.items():
            direction = "BEAT" if data["beat"] else "MISS"
            lines.append(f"  {metric.upper()}: {direction} by {abs(data['surprise_pct']):.1f}% (actual {data['actual']} vs est {data['expected']})")
        return "\n".join(lines)
