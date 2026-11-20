import logging
from typing import Optional, List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from agents.base import BaseAgent
from models.state import AgentResult, CritiqueFeedback
from config import settings

logger = logging.getLogger(__name__)


class SynthesisAgent(BaseAgent):
    name = "synthesis"

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.OPENAI_MODEL, temperature=0, api_key=settings.OPENAI_API_KEY)

    def run(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        agent_results: Dict[str, AgentResult] = kwargs.get("agent_results", {})
        return self.synthesize(query, ticker, agent_results)

    def synthesize(self, query: str, ticker: str, agent_results: Dict[str, AgentResult]) -> AgentResult:
        sections = []
        all_sources = []

        for agent_name, result in agent_results.items():
            if result and result.response:
                sections.append(f"### {agent_name.replace('_', ' ').title()} Agent\n{result.response}")
                all_sources.extend(result.sources)

        combined = "\n\n---\n\n".join(sections) if sections else "No agent outputs available."

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a senior research director synthesizing outputs from multiple specialized analysts. "
                "Create a unified, coherent earnings intelligence report. Structure: "
                "## Executive Summary (3-4 bullet points, the most important findings) "
                "## Detailed Analysis (weave together insights from all agents into a narrative) "
                "## Key Risks and Concerns "
                "## Forward Outlook "
                "## Confidence Assessment (how reliable is this analysis given data quality) "
                "Resolve any contradictions between agents by noting them explicitly. "
                "Do not simply concatenate — synthesize and add value."
            )),
            ("human", "Ticker: {ticker}\nOriginal Query: {query}\n\nAgent Outputs:\n{sections}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"ticker": ticker, "query": query, "sections": combined})

        avg_confidence = sum(r.confidence for r in agent_results.values() if r) / max(len(agent_results), 1)

        return AgentResult(
            agent_name=self.name,
            response=response.content,
            confidence=avg_confidence,
            sources=list(set(all_sources)),
        )

    def critique(self, synthesis: str, agent_results: Dict[str, AgentResult]) -> List[CritiqueFeedback]:
        sections = []
        for name, result in agent_results.items():
            if result and result.response:
                sections.append(f"[{name}]: {result.response[:500]}")
        agent_summaries = "\n\n".join(sections)

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a critical reviewer checking an earnings research report for quality. "
                "Identify: "
                "1) Internal contradictions (one agent says revenue beat, another implies weakness) "
                "2) Unsupported claims (conclusions not backed by data from any agent) "
                "3) Missing context (important questions the report should address but doesn't) "
                "4) Logical gaps (conclusions that don't follow from the evidence) "
                "For each issue, specify which agent should be re-queried and what question to ask. "
                "If the report is solid, say 'NO ISSUES FOUND'. "
                "Output as a structured list."
            )),
            ("human", "Synthesis:\n{synthesis}\n\nOriginal Agent Outputs:\n{agents}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"synthesis": synthesis, "agents": agent_summaries})

        return self._parse_critique(response.content)

    def refine(self, original_synthesis: str, feedback: List[CritiqueFeedback], follow_up_answers: Dict[str, str]) -> str:
        feedback_text = "\n".join([
            f"- [{fb.target_agent}] {fb.question} → Answer: {follow_up_answers.get(fb.target_agent, 'No answer')}"
            for fb in feedback
        ])

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are refining an earnings research report based on critique feedback. "
                "Incorporate the follow-up answers to address the identified issues. "
                "Maintain the same structure but resolve contradictions and fill gaps. "
                "If a contradiction cannot be resolved, note it as an open question."
            )),
            ("human", "Original Report:\n{synthesis}\n\nCritique Follow-ups:\n{feedback}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"synthesis": original_synthesis, "feedback": feedback_text})
        return response.content

    def _parse_critique(self, critique_text: str) -> List[CritiqueFeedback]:
        if "NO ISSUES FOUND" in critique_text.upper():
            return []

        feedbacks = []
        lines = critique_text.strip().split("\n")
        for line in lines:
            line = line.strip().lstrip("- •*0123456789.)")
            if not line:
                continue

            target = "synthesis"
            for agent_name in ["pre_earnings", "live_earnings", "transcript", "post_earnings"]:
                if agent_name.lower() in line.lower() or agent_name.replace("_", " ") in line.lower():
                    target = agent_name
                    break

            severity = "medium"
            if any(w in line.lower() for w in ["contradiction", "conflicting", "inconsistent"]):
                severity = "high"
            elif any(w in line.lower() for w in ["minor", "small", "optional"]):
                severity = "low"

            feedbacks.append(CritiqueFeedback(
                target_agent=target,
                question=line,
                severity=severity,
            ))

        return feedbacks[:5]
