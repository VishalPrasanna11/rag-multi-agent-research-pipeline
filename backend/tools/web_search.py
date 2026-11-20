import logging
from typing import Optional, List, Dict, Any
from tavily import TavilyClient
from config import settings

logger = logging.getLogger(__name__)


class WebSearchClient:
    def __init__(self):
        self.client = TavilyClient(api_key=settings.TAVILY_API_KEY)

    def search(
        self,
        query: str,
        ticker: Optional[str] = None,
        max_results: int = 5,
        search_depth: str = "advanced",
    ) -> List[Dict[str, Any]]:
        augmented_query = f"{ticker} {query}" if ticker else query

        response = self.client.search(
            query=augmented_query,
            search_depth=search_depth,
            max_results=max_results,
            include_domains=[
                "forbes.com", "cnbc.com", "bloomberg.com", "reuters.com",
                "wsj.com", "seekingalpha.com", "marketwatch.com", "fool.com",
            ],
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0),
                "published_date": item.get("published_date", ""),
            })
        return results

    def search_earnings_news(self, ticker: str, fiscal_quarter: Optional[str] = None) -> List[Dict[str, Any]]:
        query = f"{ticker} earnings results"
        if fiscal_quarter:
            query += f" {fiscal_quarter}"
        return self.search(query, ticker=ticker)

    def search_analyst_revisions(self, ticker: str) -> List[Dict[str, Any]]:
        return self.search(f"{ticker} analyst upgrade downgrade price target revision", ticker=ticker)

    def search_supply_chain(self, ticker: str, suppliers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if suppliers:
            supplier_str = " OR ".join(suppliers)
            query = f"{ticker} supply chain {supplier_str} revenue"
        else:
            query = f"{ticker} supply chain semiconductor revenue"
        return self.search(query, ticker=ticker)
