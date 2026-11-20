import logging
from typing import Optional, List, Dict, Any
import requests
from config import settings

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/api/v3"


class FMPClient:
    def __init__(self):
        self.api_key = settings.FMP_API_KEY

    def _get(self, endpoint: str, params: Optional[dict] = None) -> Any:
        params = params or {}
        params["apikey"] = self.api_key
        url = f"{FMP_BASE}/{endpoint}"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_earnings_calendar(self, from_date: str, to_date: str) -> List[Dict[str, Any]]:
        return self._get("earning_calendar", {"from": from_date, "to": to_date})

    def get_earnings_surprises(self, ticker: str) -> List[Dict[str, Any]]:
        return self._get(f"earnings-surprises/{ticker}")

    def get_analyst_estimates(self, ticker: str, period: str = "quarter", limit: int = 12) -> List[Dict[str, Any]]:
        return self._get(f"analyst-estimates/{ticker}", {"period": period, "limit": limit})

    def get_analyst_recommendations(self, ticker: str) -> List[Dict[str, Any]]:
        return self._get(f"analyst-stock-recommendations/{ticker}")

    def get_income_statement(self, ticker: str, period: str = "quarter", limit: int = 12) -> List[Dict[str, Any]]:
        return self._get(f"income-statement/{ticker}", {"period": period, "limit": limit})

    def get_balance_sheet(self, ticker: str, period: str = "quarter", limit: int = 12) -> List[Dict[str, Any]]:
        return self._get(f"balance-sheet-statement/{ticker}", {"period": period, "limit": limit})

    def get_cash_flow(self, ticker: str, period: str = "quarter", limit: int = 12) -> List[Dict[str, Any]]:
        return self._get(f"cash-flow-statement/{ticker}", {"period": period, "limit": limit})

    def get_key_metrics(self, ticker: str, period: str = "quarter", limit: int = 12) -> List[Dict[str, Any]]:
        return self._get(f"key-metrics/{ticker}", {"period": period, "limit": limit})

    def get_price_target(self, ticker: str) -> List[Dict[str, Any]]:
        return self._get(f"price-target/{ticker}")

    def get_stock_peers(self, ticker: str) -> List[str]:
        result = self._get(f"stock_peers", {"symbol": ticker})
        if result and len(result) > 0:
            return result[0].get("peersList", [])
        return []
