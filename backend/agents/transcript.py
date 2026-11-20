import logging
from typing import Optional, List
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from agents.base import BaseAgent
from models.state import AgentResult
from tools.pinecone_client import PineconeClient
from config import settings

logger = logging.getLogger(__name__)

HEDGE_WORDS = [
    "challenging", "uncertain", "cautiously optimistic", "headwinds", "softness",
    "macro uncertainty", "prudent", "conservative", "visibility", "navigating",
    "dynamic environment", "measured approach", "tempering", "moderating",
]


class TranscriptAgent(BaseAgent):
    name = "transcript"

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.OPENAI_MODEL, temperature=0, api_key=settings.OPENAI_API_KEY)
        self.pinecone = PineconeClient()

    def run(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        transcript_text = kwargs.get("transcript_text")
        sources = []
        context_parts = []
        structured = {}

        current_chunks = []
        if transcript_text:
            context_parts.append(f"Current Quarter Transcript:\n{transcript_text[:5000]}")
            structured["hedge_words"] = self._detect_hedge_words(transcript_text)
        else:
            current_chunks = self.pinecone.query_transcripts(
                query_text=query, ticker=ticker, years=years, quarters=quarters, top_k=10,
            )
            if current_chunks:
                current_text = "\n\n".join([c["text"] for c in current_chunks])
                context_parts.append(f"Current Quarter Transcript Excerpts:\n{current_text}")
                structured["hedge_words"] = self._detect_hedge_words(current_text)
                sources.append(f"Pinecone - {ticker} Transcripts")

        prior_years = [(y - 1 if q == 1 else y) for y in (years or []) for q in (quarters or [1])]
        prior_quarters = [(4 if q == 1 else q - 1) for q in (quarters or [1])]
        prior_chunks = self.pinecone.query_transcripts(
            query_text=query, ticker=ticker, years=prior_years or None, quarters=prior_quarters or None, top_k=5,
        )
        if prior_chunks:
            prior_text = "\n\n".join([c["text"] for c in prior_chunks])
            context_parts.append(f"Prior Quarter Transcript Excerpts:\n{prior_text}")

        guidance_chunks = self.pinecone.query_transcripts(
            query_text=f"{ticker} guidance outlook forward looking", ticker=ticker,
            years=years, quarters=quarters, top_k=5,
        )
        if guidance_chunks:
            guidance_text = "\n\n".join([c["text"] for c in guidance_chunks])
            context_parts.append(f"Guidance-Related Excerpts:\n{guidance_text}")

        if structured.get("hedge_words"):
            context_parts.append(f"Detected Hedge Words: {', '.join(structured['hedge_words'])}")

        combined_context = "\n\n---\n\n".join(context_parts) if context_parts else "No transcript data available."

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a senior analyst specializing in earnings call transcript analysis. "
                "Compare the current quarter's language and tone to prior quarters. "
                "Structure your analysis as: "
                "1) Management Tone Assessment (confident/cautious/defensive) "
                "2) Guidance Changes (raised/maintained/lowered, specific numbers if available) "
                "3) Hedge Word Analysis (new cautionary language vs prior quarters) "
                "4) New Topics Introduced (topics not discussed in prior calls) "
                "5) Dropped Topics (previously discussed items now absent) "
                "6) Key Quotes (one important quote from CEO and CFO each). "
                "Be specific about what changed between quarters."
            )),
            ("human", "Ticker: {ticker}\nQuery: {query}\n\n{context}"),
        ])
        chain = prompt | self.llm
        response = chain.invoke({"ticker": ticker, "query": query, "context": combined_context})

        confidence = min(0.85, 0.3 + 0.1 * len(context_parts))

        return AgentResult(
            agent_name=self.name,
            response=response.content,
            confidence=confidence,
            sources=sources or ["Pinecone - Earnings Transcripts"],
            structured_data=structured,
        )

    def _detect_hedge_words(self, text: str) -> List[str]:
        text_lower = text.lower()
        return [word for word in HEDGE_WORDS if word in text_lower]
