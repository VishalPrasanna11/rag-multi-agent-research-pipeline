import pytest
from unittest.mock import patch, MagicMock
from models.state import AgentResult


class TestBaseAgent:
    def test_execute_wraps_timing(self):
        from agents.base import BaseAgent

        class DummyAgent(BaseAgent):
            name = "dummy"

            def run(self, query, ticker, years=None, quarters=None, **kwargs):
                return AgentResult(
                    agent_name="dummy",
                    response="test response",
                    confidence=0.8,
                    sources=["test"],
                )

        agent = DummyAgent()
        result = agent.execute("test query", "NVDA")
        assert result.agent_name == "dummy"
        assert result.response == "test response"
        assert result.execution_time_ms >= 0

    def test_execute_handles_error(self):
        from agents.base import BaseAgent

        class FailAgent(BaseAgent):
            name = "fail"

            def run(self, query, ticker, years=None, quarters=None, **kwargs):
                raise ValueError("test error")

        agent = FailAgent()
        result = agent.execute("test", "NVDA")
        assert result.confidence == 0.0
        assert "Error" in result.response


class TestPreEarningsAgent:
    @patch("agents.pre_earnings.FMPClient")
    @patch("agents.pre_earnings.WebSearchClient")
    @patch("agents.pre_earnings.MarketDataClient")
    @patch("agents.pre_earnings.SnowflakeClient")
    @patch("agents.pre_earnings.ChatOpenAI")
    def test_run_returns_agent_result(self, mock_llm, mock_sf, mock_market, mock_search, mock_fmp):
        mock_llm_instance = MagicMock()
        mock_llm_instance.__or__ = MagicMock(return_value=MagicMock(
            invoke=MagicMock(return_value=MagicMock(content="Pre-earnings analysis"))
        ))
        mock_llm.return_value = mock_llm_instance

        mock_fmp.return_value.get_analyst_estimates.return_value = [
            {"estimatedEpsAvg": 0.75, "estimatedRevenueAvg": 10000000000, "date": "2024-01"}
        ]
        mock_fmp.return_value.get_earnings_surprises.return_value = []
        mock_market.return_value.get_options_chain.return_value = {}
        mock_search.return_value.search_supply_chain.return_value = []
        mock_search.return_value.search_earnings_news.return_value = []

        from agents.pre_earnings import PreEarningsAgent
        agent = PreEarningsAgent()
        result = agent.run("test", "NVDA")

        assert isinstance(result, AgentResult)
        assert result.agent_name == "pre_earnings"


class TestSynthesisAgent:
    @patch("agents.synthesis.ChatOpenAI")
    def test_synthesize_combines_agents(self, mock_llm):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = MagicMock(content="Synthesized report")
        mock_llm.return_value.__or__ = MagicMock(return_value=mock_chain)

        from agents.synthesis import SynthesisAgent
        agent = SynthesisAgent()

        agent_results = {
            "pre_earnings": AgentResult("pre_earnings", "Pre data", 0.8, ["src1"]),
            "post_earnings": AgentResult("post_earnings", "Post data", 0.7, ["src2"]),
        }
        result = agent.synthesize("test", "NVDA", agent_results)
        assert isinstance(result, AgentResult)

    @patch("agents.synthesis.ChatOpenAI")
    def test_critique_no_issues(self, mock_llm):
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = MagicMock(content="NO ISSUES FOUND")
        mock_llm.return_value.__or__ = MagicMock(return_value=mock_chain)

        from agents.synthesis import SynthesisAgent
        agent = SynthesisAgent()

        feedback = agent.critique("Good report", {})
        assert feedback == []
