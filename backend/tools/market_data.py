import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


class MarketDataClient:
    def get_stock_prices(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        df.reset_index(inplace=True)
        df["Ticker"] = ticker
        return df

    def get_daily_prices(self, ticker: str, start_date: str, end_date: Optional[str] = None) -> pd.DataFrame:
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        stock = yf.Ticker(ticker)
        df = stock.history(start=start_date, end=end_date)
        df.reset_index(inplace=True)
        df["Ticker"] = ticker
        return df

    def get_options_chain(self, ticker: str) -> Dict[str, Any]:
        stock = yf.Ticker(ticker)
        try:
            expirations = stock.options
            if not expirations:
                return {"implied_move_pct": None, "expirations": []}

            nearest_expiry = expirations[0]
            chain = stock.option_chain(nearest_expiry)

            atm_calls = chain.calls.iloc[(chain.calls["strike"] - stock.info.get("currentPrice", 0)).abs().argsort()[:3]]
            atm_puts = chain.puts.iloc[(chain.puts["strike"] - stock.info.get("currentPrice", 0)).abs().argsort()[:3]]

            avg_call_iv = atm_calls["impliedVolatility"].mean()
            avg_put_iv = atm_puts["impliedVolatility"].mean()
            avg_iv = (avg_call_iv + avg_put_iv) / 2

            days_to_expiry = (datetime.strptime(nearest_expiry, "%Y-%m-%d") - datetime.now()).days
            implied_move_pct = avg_iv * (days_to_expiry / 365) ** 0.5 * 100

            return {
                "implied_move_pct": round(implied_move_pct, 2),
                "avg_iv": round(avg_iv * 100, 2),
                "nearest_expiry": nearest_expiry,
                "current_price": stock.info.get("currentPrice"),
                "expirations": list(expirations[:5]),
            }
        except Exception as e:
            logger.error(f"Options chain error for {ticker}: {e}")
            return {"implied_move_pct": None, "expirations": [], "error": str(e)}

    def get_stock_info(self, ticker: str) -> Dict[str, Any]:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "ticker": ticker,
            "name": info.get("longName", ticker),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap"),
            "current_price": info.get("currentPrice"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
        }

    def get_post_earnings_reaction(self, ticker: str, earnings_date: str, days: int = 5) -> pd.DataFrame:
        start = datetime.strptime(earnings_date, "%Y-%m-%d")
        end = start + timedelta(days=days + 5)  # extra buffer for weekends
        df = self.get_daily_prices(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        return df.head(days + 1)
