import logging
from typing import Optional, List, Dict, Any
from pinecone import Pinecone
from langchain_openai import OpenAIEmbeddings
from config import settings

logger = logging.getLogger(__name__)


class PineconeClient:
    def __init__(self):
        self.pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self.embeddings = OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            openai_api_key=settings.OPENAI_API_KEY,
        )

    def _get_index(self, index_name: str):
        return self.pc.Index(index_name)

    def query_transcripts(
        self,
        query_text: str,
        ticker: str,
        years: Optional[List[int]] = None,
        quarters: Optional[List[int]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        query_embedding = self.embeddings.embed_query(query_text)
        index = self._get_index(settings.PINECONE_TRANSCRIPTS_INDEX)

        filter_dict: Dict[str, Any] = {"ticker": ticker}
        if years:
            filter_dict["fiscal_year"] = {"$in": years}
        if quarters:
            filter_dict["fiscal_quarter"] = {"$in": quarters}

        results = index.query(
            vector=query_embedding,
            filter=filter_dict,
            top_k=top_k,
            include_metadata=True,
            namespace=ticker,
        )

        contexts = []
        for match in results.matches:
            contexts.append({
                "text": match.metadata.get("text", ""),
                "score": match.score,
                "ticker": match.metadata.get("ticker", ""),
                "fiscal_year": match.metadata.get("fiscal_year", ""),
                "fiscal_quarter": match.metadata.get("fiscal_quarter", ""),
                "section": match.metadata.get("section", ""),
                "speaker": match.metadata.get("speaker", ""),
                "speaker_role": match.metadata.get("speaker_role", ""),
            })
        return contexts

    def query_filings(
        self,
        query_text: str,
        ticker: str,
        filing_type: Optional[str] = None,
        years: Optional[List[int]] = None,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        query_embedding = self.embeddings.embed_query(query_text)
        index = self._get_index(settings.PINECONE_FILINGS_INDEX)

        filter_dict: Dict[str, Any] = {"ticker": ticker}
        if filing_type:
            filter_dict["filing_type"] = filing_type
        if years:
            filter_dict["fiscal_year"] = {"$in": years}

        results = index.query(
            vector=query_embedding,
            filter=filter_dict,
            top_k=top_k,
            include_metadata=True,
            namespace=ticker,
        )

        contexts = []
        for match in results.matches:
            contexts.append({
                "text": match.metadata.get("text", ""),
                "score": match.score,
                "ticker": match.metadata.get("ticker", ""),
                "filing_type": match.metadata.get("filing_type", ""),
                "fiscal_year": match.metadata.get("fiscal_year", ""),
                "section": match.metadata.get("section", ""),
            })
        return contexts

    def upsert_transcript_chunks(self, chunks: List[Dict[str, Any]], ticker: str):
        index = self._get_index(settings.PINECONE_TRANSCRIPTS_INDEX)
        vectors = []
        for chunk in chunks:
            embedding = self.embeddings.embed_query(chunk["text"])
            vectors.append({
                "id": chunk["id"],
                "values": embedding,
                "metadata": {
                    "text": chunk["text"],
                    "ticker": ticker,
                    "fiscal_year": chunk["fiscal_year"],
                    "fiscal_quarter": chunk["fiscal_quarter"],
                    "section": chunk["section"],
                    "speaker": chunk.get("speaker", ""),
                    "speaker_role": chunk.get("speaker_role", ""),
                    "date": chunk.get("date", ""),
                },
            })
        for i in range(0, len(vectors), 100):
            batch = vectors[i:i + 100]
            index.upsert(vectors=batch, namespace=ticker)
        logger.info(f"Upserted {len(vectors)} transcript chunks for {ticker}")

    def upsert_filing_chunks(self, chunks: List[Dict[str, Any]], ticker: str):
        index = self._get_index(settings.PINECONE_FILINGS_INDEX)
        vectors = []
        for chunk in chunks:
            embedding = self.embeddings.embed_query(chunk["text"])
            vectors.append({
                "id": chunk["id"],
                "values": embedding,
                "metadata": {
                    "text": chunk["text"],
                    "ticker": ticker,
                    "filing_type": chunk["filing_type"],
                    "fiscal_year": chunk["fiscal_year"],
                    "fiscal_quarter": chunk.get("fiscal_quarter"),
                    "section": chunk["section"],
                    "filed_date": chunk.get("filed_date", ""),
                },
            })
        for i in range(0, len(vectors), 100):
            batch = vectors[i:i + 100]
            index.upsert(vectors=batch, namespace=ticker)
        logger.info(f"Upserted {len(vectors)} filing chunks for {ticker}")

    def health_check(self) -> bool:
        try:
            self.pc.list_indexes()
            return True
        except Exception:
            return False
