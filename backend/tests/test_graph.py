import pytest
from models.state import EarningsState, AgentResult, CritiqueFeedback
from graph.edges import route_by_mode, should_critique


class TestRouteByMode:
    def test_full_cycle(self):
        state = {"mode": "full_cycle", "active_agents": []}
        result = route_by_mode(state)
        assert len(result) == 4

    def test_pre_only(self):
        state = {"mode": "pre_only", "active_agents": []}
        result = route_by_mode(state)
        assert result == ["pre_earnings_node"]

    def test_post_only(self):
        state = {"mode": "post_only", "active_agents": []}
        result = route_by_mode(state)
        assert result == ["post_earnings_node"]

    def test_custom_agents(self):
        state = {"mode": "full_cycle", "active_agents": ["pre_earnings_node", "transcript_node"]}
        result = route_by_mode(state)
        assert result == ["pre_earnings_node", "transcript_node"]


class TestShouldCritique:
    def test_max_rounds_reached(self):
        state = {"critique_round": 2, "critique_feedback": []}
        result = should_critique(state)
        assert result == "human_review"

    def test_no_feedback(self):
        state = {"critique_round": 0, "critique_feedback": []}
        result = should_critique(state)
        assert result == "human_review"

    def test_high_severity_triggers_refine(self):
        state = {
            "critique_round": 0,
            "critique_feedback": [
                CritiqueFeedback(target_agent="pre_earnings", question="Contradiction found", severity="high"),
            ],
        }
        result = should_critique(state)
        assert result == "refine"
