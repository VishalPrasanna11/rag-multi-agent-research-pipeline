import logging
import time
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
import feedparser
import requests

logger = logging.getLogger(__name__)

SEC_RSS_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_FULL_TEXT_SEARCH = "https://efts.sec.gov/LATEST/search-index"


class SECMonitor:
    def __init__(self, watched_tickers: Optional[List[str]] = None):
        self.watched_tickers = set(watched_tickers or [])
        self.seen_accessions: set = set()
        self._callbacks: List[Callable] = []

    def add_ticker(self, ticker: str):
        self.watched_tickers.add(ticker.upper())

    def remove_ticker(self, ticker: str):
        self.watched_tickers.discard(ticker.upper())

    def on_filing(self, callback: Callable):
        self._callbacks.append(callback)

    def check_recent_filings(self, ticker: str, filing_type: str = "8-K") -> List[Dict[str, Any]]:
        url = (
            f"{SEC_RSS_BASE}?action=getcompany&company={ticker}"
            f"&type={filing_type}&dateb=&owner=include&count=10&search_text=&action=getcompany&output=atom"
        )
        try:
            feed = feedparser.parse(url)
            filings = []
            for entry in feed.entries:
                filing = {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "updated": entry.get("updated", ""),
                    "accession": entry.get("id", ""),
                    "ticker": ticker,
                    "filing_type": filing_type,
                }
                filings.append(filing)
            return filings
        except Exception as e:
            logger.error(f"SEC RSS error for {ticker}: {e}")
            return []

    def download_filing(self, filing_url: str) -> Optional[str]:
        try:
            headers = {"User-Agent": "EarningsIntel Research Bot research@example.com"}
            resp = requests.get(filing_url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"Filing download error: {e}")
            return None

    def poll_once(self) -> List[Dict[str, Any]]:
        new_filings = []
        for ticker in self.watched_tickers:
            filings = self.check_recent_filings(ticker, "8-K")
            for filing in filings:
                accession = filing["accession"]
                if accession not in self.seen_accessions:
                    self.seen_accessions.add(accession)
                    new_filings.append(filing)
                    for cb in self._callbacks:
                        try:
                            cb(filing)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
        return new_filings

    def poll_loop(self, interval_seconds: int = 60):
        logger.info(f"Starting SEC RSS poll loop for: {self.watched_tickers}")
        while True:
            new = self.poll_once()
            if new:
                logger.info(f"Found {len(new)} new filings")
            time.sleep(interval_seconds)
