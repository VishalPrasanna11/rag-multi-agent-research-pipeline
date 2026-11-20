import logging
from typing import Optional
from tools.pinecone_client import PineconeClient
from tools.pdf_parser import PDFParser

logger = logging.getLogger(__name__)


def ingest_transcript(
    ticker: str,
    text: str,
    year: int,
    quarter: int,
    source: str = "manual",
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
):
    parser = PDFParser()
    pinecone = PineconeClient()

    chunks = parser.chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
    logger.info(f"Split transcript into {len(chunks)} chunks for {ticker} Q{quarter} {year}")

    documents = []
    for i, chunk in enumerate(chunks):
        documents.append({
            "id": f"{ticker}-{year}-Q{quarter}-{i:04d}",
            "text": chunk,
            "metadata": {
                "ticker": ticker,
                "year": year,
                "quarter": quarter,
                "chunk_index": i,
                "source": source,
            },
        })

    pinecone.upsert_transcript_chunks(ticker, documents)
    logger.info(f"Upserted {len(documents)} transcript chunks for {ticker} Q{quarter} {year}")
    return len(documents)


def ingest_transcript_pdf(
    ticker: str,
    pdf_path: str,
    year: int,
    quarter: int,
):
    parser = PDFParser()
    text = parser.extract_text(pdf_path)
    if not text.strip():
        logger.warning(f"No text extracted from {pdf_path}")
        return 0
    return ingest_transcript(ticker, text, year, quarter, source=f"pdf:{pdf_path}")


def ingest_filing(
    ticker: str,
    text: str,
    filing_type: str,
    filing_date: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
):
    parser = PDFParser()
    pinecone = PineconeClient()

    chunks = parser.chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
    logger.info(f"Split {filing_type} filing into {len(chunks)} chunks for {ticker}")

    documents = []
    for i, chunk in enumerate(chunks):
        documents.append({
            "id": f"{ticker}-{filing_type}-{filing_date}-{i:04d}",
            "text": chunk,
            "metadata": {
                "ticker": ticker,
                "filing_type": filing_type,
                "filing_date": filing_date,
                "chunk_index": i,
            },
        })

    pinecone.upsert_filing_chunks(documents)
    logger.info(f"Upserted {len(documents)} filing chunks for {ticker} {filing_type}")
    return len(documents)
