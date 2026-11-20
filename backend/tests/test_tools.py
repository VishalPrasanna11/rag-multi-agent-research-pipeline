import pytest
from unittest.mock import patch, MagicMock


class TestPDFParser:
    def test_extract_earnings_metrics_eps(self):
        from tools.pdf_parser import PDFParser
        parser = PDFParser()
        text = "diluted earnings per share of $0.75 for the quarter"
        metrics = parser.extract_earnings_metrics(text)
        assert metrics["eps"] == 0.75

    def test_extract_earnings_metrics_revenue(self):
        from tools.pdf_parser import PDFParser
        parser = PDFParser()
        text = "total revenue was $35.1 billion for the quarter"
        metrics = parser.extract_earnings_metrics(text)
        assert metrics["revenue_millions"] == 35100.0

    def test_chunk_text(self):
        from tools.pdf_parser import PDFParser
        parser = PDFParser()
        text = "word " * 500
        chunks = parser.chunk_text(text, chunk_size=100, overlap=20)
        assert len(chunks) > 1
        assert all(len(c) <= 120 for c in chunks)


class TestMarketDataClient:
    @patch("tools.market_data.yf.Ticker")
    def test_get_stock_info(self, mock_ticker):
        mock_ticker.return_value.info = {"currentPrice": 150, "marketCap": 1e12}
        from tools.market_data import MarketDataClient
        client = MarketDataClient()
        info = client.get_stock_info("NVDA")
        assert info["currentPrice"] == 150


class TestWebSearchClient:
    @patch("tools.web_search.TavilyClient")
    def test_search(self, mock_tavily):
        mock_tavily.return_value.search.return_value = {
            "results": [{"title": "Test", "url": "http://test.com", "content": "content"}]
        }
        from tools.web_search import WebSearchClient
        client = WebSearchClient()
        results = client.search("NVDA earnings")
        assert len(results) == 1
        assert results[0]["title"] == "Test"
