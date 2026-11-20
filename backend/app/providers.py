"""Data providers behind one interface. Live implementations + a fixture replayer.

Every call returns a `Payload` carrying the data plus provenance (source, url, title, as_of), so the
gatherers can cite it. No API key is ever placed in a URL that could be logged: FMP keys are passed
via httpx `params` and every log line goes through `redact()`.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.logging import get_logger, redact
from app.schemas import SourceKind

log = get_logger("providers")

FIXTURE_DIR = Path(__file__).parent / "fixtures"
YF_LICENSE = (
    "Yahoo Finance via yfinance (unofficial; Yahoo terms: personal use). Verify before redistribution."
)
FMP_LICENSE = "Financial Modeling Prep (free tier; display only, no redistribution)."
SEC_LICENSE = "SEC EDGAR (public domain U.S. government data)."


class ProviderError(Exception):
    def __init__(self, source: str, message: str, retryable: bool = True) -> None:
        super().__init__(redact(f"{source}: {message}"))
        self.source = source
        self.retryable = retryable


class NotFound(ProviderError):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(source, message, retryable=False)


@dataclass
class Payload:
    data: Any
    source: SourceKind
    title: str
    as_of: datetime
    url: str | None = None
    locator: str | None = None
    license_note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Providers(Protocol):
    name: str

    def resolve(self, ticker: str) -> Payload: ...
    def submissions(self, cik: str) -> Payload: ...
    def company_facts(self, cik: str) -> Payload: ...
    def filing_exhibits(self, cik: str, accession: str) -> Payload: ...
    def document_text(self, url: str) -> Payload: ...
    def earnings_history(self, ticker: str) -> Payload: ...
    def prices(self, ticker: str, start: date, end: date) -> Payload: ...
    def options_snapshot(self, ticker: str, event_date: date | None) -> Payload: ...
    def short_interest(self, ticker: str) -> Payload: ...
    def news(self, ticker: str, start: date, end: date) -> Payload: ...
    def form4(self, cik: str, accession: str, primary_doc: str) -> Payload: ...
    def earnings_calendar(self, start: date, end: date) -> Payload: ...


# ---------------------------------------------------------------- shared HTTP helpers


class _RateLimiter:
    """Token bucket shared across threads (EDGAR fair access: <= 10 req/s; we default to 8)."""

    def __init__(self, rps: float) -> None:
        self.min_interval = 1.0 / max(rps, 0.1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if delay > 0:
            time.sleep(delay)


_sec_limiter: _RateLimiter | None = None


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    # keep table rows on their own lines so reconciliation tables stay legible
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with(soup.new_string(" | ".join(c for c in cells if c) + "\n"))
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _cache_get(key: str) -> Any | None:
    try:
        from app.db import ProviderCache, session_scope

        with session_scope() as s:
            row = s.get(ProviderCache, key)
            if row and row.expires_at > datetime.now(UTC):
                return row.value
    except Exception as e:  # cache is best-effort
        log.debug("cache_get_failed", error=str(e))
    return None


def _cache_put(key: str, value: Any, ttl: timedelta) -> None:
    try:
        from app.db import ProviderCache, session_scope

        with session_scope() as s:
            now = datetime.now(UTC)
            row = s.get(ProviderCache, key)
            if row:
                row.value, row.fetched_at, row.expires_at = value, now, now + ttl
            else:
                s.add(ProviderCache(key=key, value=value, fetched_at=now, expires_at=now + ttl))
    except Exception as e:
        log.debug("cache_put_failed", error=str(e))


def _ck(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return raw if len(raw) < 250 else raw[:200] + hashlib.sha1(raw.encode()).hexdigest()  # noqa: S324


# ---------------------------------------------------------------- live providers


class LiveProviders:
    name = "live"

    def __init__(self, settings: Settings | None = None) -> None:
        global _sec_limiter
        self.s = settings or get_settings()
        if _sec_limiter is None:
            _sec_limiter = _RateLimiter(self.s.SEC_MAX_RPS)
        self._sec = httpx.Client(
            headers={"User-Agent": self.s.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=30,
            follow_redirects=True,
        )
        self._http = httpx.Client(timeout=30, follow_redirects=True)

    # ---- SEC EDGAR
    @retry(
        retry=retry_if_exception_type(ProviderError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        reraise=True,
    )
    def _sec_get(self, url: str) -> httpx.Response:
        assert _sec_limiter is not None
        _sec_limiter.wait()
        try:
            r = self._sec.get(url)
        except httpx.HTTPError as e:
            raise ProviderError("sec", f"network error {type(e).__name__}") from e
        if r.status_code == 404:
            raise NotFound("sec", f"404 {url}")
        if r.status_code in (403, 429) or r.status_code >= 500:
            raise ProviderError("sec", f"HTTP {r.status_code} for {url}")
        r.raise_for_status()
        return r

    def _sec_json(self, url: str, ttl: timedelta) -> Any:
        key = _ck("sec", url)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        data = self._sec_get(url).json()
        _cache_put(key, data, ttl)
        return data

    def resolve(self, ticker: str) -> Payload:
        data = self._sec_json("https://www.sec.gov/files/company_tickers.json", timedelta(days=1))
        t = ticker.upper().replace(".", "-")
        for row in data.values():
            if str(row["ticker"]).upper() == t:
                cik = str(row["cik_str"]).zfill(10)
                return Payload(
                    {"ticker": ticker.upper(), "cik": cik, "name": row["title"]},
                    SourceKind.SEC_FILING,
                    "SEC company_tickers.json",
                    datetime.now(UTC),
                    url="https://www.sec.gov/files/company_tickers.json",
                    license_note=SEC_LICENSE,
                )
        raise NotFound("sec", f"ticker {ticker} not found in SEC company list (non-US or invalid?)")

    def submissions(self, cik: str) -> Payload:
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        d = self._sec_json(url, timedelta(minutes=10))
        r = d["filings"]["recent"]
        filings = [
            {
                "accession": r["accessionNumber"][i],
                "form": r["form"][i],
                "filing_date": r["filingDate"][i],
                "report_date": r["reportDate"][i],
                "accepted": r["acceptanceDateTime"][i],
                "items": r["items"][i],
                "primary_doc": r["primaryDocument"][i],
            }
            for i in range(len(r["form"]))
        ]
        meta = {
            "name": d.get("name"),
            "fiscal_year_end": d.get("fiscalYearEnd"),
            "sic": d.get("sic"),
            "sic_description": d.get("sicDescription"),
            "tickers": d.get("tickers"),
            "exchanges": d.get("exchanges"),
            "former_names": d.get("formerNames"),
        }
        return Payload(
            {"meta": meta, "filings": filings},
            SourceKind.SEC_FILING,
            f"SEC submissions CIK{cik}",
            datetime.now(UTC),
            url=url,
            license_note=SEC_LICENSE,
        )

    def company_facts(self, cik: str) -> Payload:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        d = self._sec_json(url, timedelta(hours=6))
        return Payload(
            prune_company_facts(d),
            SourceKind.SEC_XBRL,
            f"SEC XBRL company facts CIK{cik}",
            datetime.now(UTC),
            url=url,
            license_note=SEC_LICENSE,
        )

    def filing_exhibits(self, cik: str, accession: str) -> Payload:
        acc = accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
        key = _ck("sec-ex", accession)
        cached = _cache_get(key)
        if cached is None:
            html = self._sec_get(f"{base}/{accession}-index.html").text
            cached = parse_filing_index(html, base)
            _cache_put(key, cached, timedelta(days=30))
        return Payload(
            cached,
            SourceKind.SEC_FILING,
            f"SEC filing index {accession}",
            datetime.now(UTC),
            url=f"{base}/{accession}-index.html",
            locator=accession,
            license_note=SEC_LICENSE,
        )

    def document_text(self, url: str) -> Payload:
        key = _ck("sec-doc", url)
        text = _cache_get(key)
        if text is None:
            text = _html_to_text(self._sec_get(url).text)
            _cache_put(key, text, timedelta(days=90))
        return Payload(
            text,
            SourceKind.SEC_FILING,
            url.rsplit("/", 1)[-1],
            datetime.now(UTC),
            url=url,
            license_note=SEC_LICENSE,
        )

    def form4(self, cik: str, accession: str, primary_doc: str) -> Payload:
        doc = primary_doc.rsplit("/", 1)[
            -1
        ]  # primaryDocument points at the XSL rendering; raw XML sits at root
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"
        key = _ck("sec-f4", accession)
        data = _cache_get(key)
        if data is None:
            data = parse_form4(self._sec_get(url).text)
            _cache_put(key, data, timedelta(days=365))
        return Payload(
            data,
            SourceKind.SEC_FILING,
            f"Form 4 {accession}",
            datetime.now(UTC),
            url=url,
            locator=accession,
            license_note=SEC_LICENSE,
        )

    # ---- FMP (stable API)
    def _fmp(self, path: str, params: dict[str, Any], ttl: timedelta) -> Any:
        if not self.s.FMP_API_KEY:
            raise ProviderError("fmp", "FMP_API_KEY not configured", retryable=False)
        key = _ck("fmp", path, json.dumps(params, sort_keys=True))
        cached = _cache_get(key)
        if cached is not None:
            return cached
        try:
            r = self._http.get(
                f"https://financialmodelingprep.com/stable/{path}",
                params={**params, "apikey": self.s.FMP_API_KEY},
            )
        except httpx.HTTPError as e:
            raise ProviderError("fmp", f"network error {type(e).__name__}") from e
        if r.status_code in (401, 402, 403):
            raise ProviderError(
                "fmp", f"HTTP {r.status_code} on /stable/{path} (plan limit)", retryable=False
            )
        if r.status_code >= 400:
            raise ProviderError("fmp", f"HTTP {r.status_code} on /stable/{path}")
        data = r.json()
        _cache_put(key, data, ttl)
        return data

    def earnings_history(self, ticker: str) -> Payload:
        rows: list[dict[str, Any]] = []
        try:
            data = self._fmp("earnings", {"symbol": ticker}, timedelta(hours=6))  # free tier: no `limit`
            for d in data:
                rows.append(
                    {
                        "date": d.get("date"),
                        "eps_est": d.get("epsEstimated"),
                        "eps_act": d.get("epsActual"),
                        "rev_est": d.get("revenueEstimated"),
                        "rev_act": d.get("revenueActual"),
                        "time": None,
                    }
                )
            src, title, lic = SourceKind.FMP, f"FMP earnings (actual vs estimate) {ticker}", FMP_LICENSE
            url = f"https://financialmodelingprep.com/stable/earnings?symbol={ticker}"
        except ProviderError as e:
            log.warning("fmp_earnings_failed_fallback_yf", ticker=ticker, error=str(e))
            rows, url = self._yf_earnings_dates(ticker), f"https://finance.yahoo.com/quote/{ticker}"
            src, title, lic = SourceKind.YFINANCE, f"Yahoo earnings dates {ticker}", YF_LICENSE
        # time of day from yfinance timestamps when available
        if self.s.ENABLE_YFINANCE and src == SourceKind.FMP:
            try:
                times = {r["date"]: r["time"] for r in self._yf_earnings_dates(ticker)}
                for r in rows:
                    r["time"] = times.get(r["date"])
            except Exception as e:
                log.info("yf_times_unavailable", error=str(e))
        return Payload(rows, src, title, datetime.now(UTC), url=url, license_note=lic)

    def _yf_earnings_dates(self, ticker: str) -> list[dict[str, Any]]:
        if not self.s.ENABLE_YFINANCE:
            raise ProviderError("yfinance", "disabled", retryable=False)
        key = _ck("yf-ed", ticker)
        cached = _cache_get(key)
        if cached is not None:
            return list(cached)
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=40)
        out = []
        if df is not None:
            for ts, row in df.iterrows():
                ts_ny = ts.tz_convert("America/New_York") if ts.tzinfo else ts
                hour = ts_ny.hour
                tod = (
                    "bmo"
                    if hour < 9 or (hour == 9 and ts_ny.minute < 30)
                    else ("amc" if hour >= 16 else "dmh")
                )
                out.append(
                    {
                        "date": ts_ny.date().isoformat(),
                        "eps_est": _f(row.get("EPS Estimate")),
                        "eps_act": _f(row.get("Reported EPS")),
                        "rev_est": None,
                        "rev_act": None,
                        "time": tod,
                    }
                )
        _cache_put(key, out, timedelta(hours=12))
        return out

    def earnings_calendar(self, start: date, end: date) -> Payload:
        # free tier rejects wide from/to ranges; the default window covers ~3 months around today
        data = self._fmp("earnings-calendar", {}, timedelta(hours=3))
        data = [r for r in data if start.isoformat() <= str(r.get("date", "")) <= end.isoformat()]
        return Payload(
            data,
            SourceKind.FMP,
            "FMP earnings calendar",
            datetime.now(UTC),
            url="https://financialmodelingprep.com/stable/earnings-calendar",
            license_note=FMP_LICENSE,
        )

    # ---- yfinance
    def prices(self, ticker: str, start: date, end: date) -> Payload:
        if not self.s.ENABLE_YFINANCE:
            raise ProviderError("yfinance", "disabled", retryable=False)
        key = _ck("yf-px", ticker, start, end)
        bars = _cache_get(key)
        if bars is None:
            import yfinance as yf

            try:
                df = yf.Ticker(ticker).history(
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    auto_adjust=True,
                    actions=True,
                )
            except Exception as e:
                raise ProviderError("yfinance", f"history failed: {type(e).__name__}") from e
            if df is None or df.empty:
                raise NotFound("yfinance", f"no price history for {ticker}")
            bars = [
                {
                    "day": ts.date().isoformat(),
                    "open": float(r["Open"]),
                    "high": float(r["High"]),
                    "low": float(r["Low"]),
                    "close": float(r["Close"]),
                    "volume": float(r["Volume"]),
                    "split": float(r.get("Stock Splits", 0) or 0),
                }
                for ts, r in df.iterrows()
            ]
            ttl = timedelta(hours=12) if end < date.today() else timedelta(minutes=30)
            _cache_put(key, bars, ttl)
        return Payload(
            bars,
            SourceKind.YFINANCE,
            f"Yahoo daily prices {ticker} (split-adjusted)",
            datetime.now(UTC),
            url=f"https://finance.yahoo.com/quote/{ticker}/history",
            license_note=YF_LICENSE,
        )

    def options_snapshot(self, ticker: str, event_date: date | None) -> Payload:
        if not self.s.ENABLE_YFINANCE:
            raise ProviderError("yfinance", "disabled", retryable=False)
        import yfinance as yf

        tk = yf.Ticker(ticker)
        try:
            expiries = list(tk.options)
            spot_df = tk.history(period="5d")
        except Exception as e:
            raise ProviderError("yfinance", f"options failed: {type(e).__name__}") from e
        if not expiries or spot_df.empty:
            raise NotFound("yfinance", "no listed options")
        spot = float(spot_df["Close"].iloc[-1])
        chains = []
        for exp in expiries[:6]:
            ch = tk.option_chain(exp)
            calls = ch.calls[
                ["strike", "lastPrice", "bid", "ask", "impliedVolatility", "openInterest", "volume"]
            ]
            puts = ch.puts[
                ["strike", "lastPrice", "bid", "ask", "impliedVolatility", "openInterest", "volume"]
            ]
            chains.append(
                {
                    "expiry": exp,
                    "calls": calls.fillna(0).to_dict("records"),
                    "puts": puts.fillna(0).to_dict("records"),
                }
            )
        return Payload(
            {"spot": spot, "chains": chains},
            SourceKind.YFINANCE,
            f"Yahoo options chain {ticker}",
            datetime.now(UTC),
            url=f"https://finance.yahoo.com/quote/{ticker}/options",
            license_note=YF_LICENSE,
        )

    def short_interest(self, ticker: str) -> Payload:
        if not self.s.ENABLE_YFINANCE:
            raise ProviderError("yfinance", "disabled", retryable=False)
        import yfinance as yf

        try:
            info = yf.Ticker(ticker).info
        except Exception as e:
            raise ProviderError("yfinance", f"info failed: {type(e).__name__}") from e
        keys = [
            "sharesShort",
            "shortRatio",
            "shortPercentOfFloat",
            "dateShortInterest",
            "sharesShortPriorMonth",
            "floatShares",
            "sector",
            "industry",
            "marketCap",
            "currency",
            "quoteType",
        ]
        data = {k: info.get(k) for k in keys}
        as_of = (
            datetime.fromtimestamp(data["dateShortInterest"], UTC)
            if data.get("dateShortInterest")
            else datetime.now(UTC)
        )
        return Payload(
            data,
            SourceKind.YFINANCE,
            f"Yahoo short interest {ticker} (FINRA, bi-monthly)",
            as_of,
            url=f"https://finance.yahoo.com/quote/{ticker}/key-statistics",
            license_note=YF_LICENSE,
        )

    # ---- Tavily (date-bounded)
    def news(self, ticker: str, start: date, end: date) -> Payload:
        if not self.s.TAVILY_API_KEY:
            raise ProviderError("tavily", "TAVILY_API_KEY not configured", retryable=False)
        from tavily import TavilyClient

        try:
            res = TavilyClient(api_key=self.s.TAVILY_API_KEY).search(
                query=f"{ticker} earnings",
                topic="news",
                max_results=8,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
        except Exception as e:
            raise ProviderError("tavily", f"search failed: {type(e).__name__}") from e
        items = []
        for it in res.get("results", []):
            pub = it.get("published_date")
            items.append(
                {
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "published": pub,
                    "snippet": (it.get("content") or "")[:400],
                }
            )
        return Payload(
            items,
            SourceKind.TAVILY,
            f"News search {ticker}",
            datetime.now(UTC),
            license_note="Third-party news; headlines and links only.",
        )


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


XBRL_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
    "InterestAndDividendIncomeOperating",
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "GrossProfit",
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeTaxExpenseBenefit",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "AccountsReceivableNetCurrent",
    "InventoryNet",
    "ContractWithCustomerLiabilityCurrent",
    "DeferredRevenueCurrent",
    "ResearchAndDevelopmentExpense",
    "SellingGeneralAndAdministrativeExpense",
    "PaymentsForRepurchaseOfCommonStock",
    "ShareBasedCompensation",
    "StockholdersEquity",
    "LongTermDebtNoncurrent",
    "CashAndCashEquivalentsAtCarryingValue",
]


def prune_company_facts(d: dict[str, Any]) -> dict[str, Any]:
    """Keep only the tags we use (company facts can be > 10 MB)."""
    gaap = d.get("facts", {}).get("us-gaap", {})
    out: dict[str, Any] = {"cik": d.get("cik"), "entityName": d.get("entityName"), "facts": {}}
    for tag in XBRL_TAGS:
        if tag in gaap:
            units = gaap[tag].get("units", {})
            u = (
                "USD/shares"
                if "USD/shares" in units
                else ("USD" if "USD" in units else next(iter(units), None))
            )
            if u:
                out["facts"][tag] = {"unit": u, "values": units[u][-80:]}
    dei = d.get("facts", {}).get("dei", {})
    if "EntityCommonStockSharesOutstanding" in dei:
        units = dei["EntityCommonStockSharesOutstanding"]["units"]
        out["facts"]["dei:SharesOutstanding"] = {"unit": "shares", "values": next(iter(units.values()))[-12:]}
    return out


def parse_form4(xml: str) -> dict[str, Any]:
    """Non-derivative transactions from a Form 4 (ownership XML)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(xml, "xml")

    def t(node: Any, path: str) -> str | None:
        cur = node
        for part in path.split("/"):
            cur = cur.find(part) if cur is not None else None
        return cur.get_text(strip=True) if cur is not None else None

    owner = t(soup, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = soup.find("reportingOwnerRelationship")
    role = "director" if rel is not None and t(rel, "isDirector") in ("1", "true") else ""
    if rel is not None and t(rel, "isOfficer") in ("1", "true"):
        role = t(rel, "officerTitle") or "officer"
    plan = (t(soup, "aff10b5One") or "").lower() in ("1", "true") or "10b5-1" in xml.lower()
    txs = []
    for tx in soup.find_all("nonDerivativeTransaction"):
        try:
            shares = float(t(tx, "transactionAmounts/transactionShares/value") or 0)
            price = float(t(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        txs.append(
            {
                "date": t(tx, "transactionDate/value"),
                "code": t(tx, "transactionCoding/transactionCode"),
                "ad": t(tx, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                "shares": shares,
                "price": price,
            }
        )
    return {"owner": owner, "role": role, "plan_10b5_1": plan, "transactions": txs}


def parse_filing_index(html: str, base: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    docs = []
    for tr in soup.select("table.tableFile tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = tds[2].find("a")
        if not a:
            continue
        href = str(a.get("href", ""))
        name = href.rsplit("/", 1)[-1]
        docs.append(
            {
                "type": tds[3].get_text(strip=True),
                "description": tds[1].get_text(strip=True),
                "name": name,
                "url": f"{base}/{name}",
            }
        )
    return docs


# ---------------------------------------------------------------- fixture providers


class FixtureProviders:
    """Replays recorded provider payloads from backend/app/fixtures/<TICKER>.json.

    Fixture files are produced by `python -m app.tools.record_fixture TICKER` from live sources (SEC data is
    public domain; market data snapshots are small test samples, not for redistribution).
    """

    name = "fixture"

    def __init__(self, fixture_dir: Path = FIXTURE_DIR, fail: set[str] | None = None) -> None:
        self.dir = fixture_dir
        self.fail = fail or set()  # simulate outages: {"prices", "news", ...}
        self._cache: dict[str, dict[str, Any]] = {}

    def _load(self, ticker: str) -> dict[str, Any]:
        t = ticker.upper()
        if t not in self._cache:
            p = self.dir / f"{t}.json"
            if not p.exists():
                raise NotFound("fixture", f"no fixture for {t}")
            self._cache[t] = json.loads(p.read_text())
        return self._cache[t]

    def _by_cik(self, cik: str) -> dict[str, Any]:
        for p in sorted(self.dir.glob("*.json")):
            d = self._cache.get(p.stem) or json.loads(p.read_text())
            if d.get("resolve", {}).get("data", {}).get("cik") == cik:
                self._cache[p.stem] = d
                return d
        raise NotFound("fixture", f"no fixture for CIK {cik}")

    def _payload(self, rec: dict[str, Any], op: str) -> Payload:
        if op in self.fail:
            raise ProviderError(op, "simulated outage")
        if rec is None:
            raise NotFound("fixture", f"{op} not recorded")
        return Payload(
            data=rec["data"],
            source=SourceKind(rec["source"]),
            title=rec["title"],
            as_of=datetime.fromisoformat(rec["as_of"]),
            url=rec.get("url"),
            locator=rec.get("locator"),
            license_note=rec.get("license_note"),
        )

    def resolve(self, ticker: str) -> Payload:
        return self._payload(self._load(ticker).get("resolve"), "resolve")  # type: ignore[arg-type]

    def submissions(self, cik: str) -> Payload:
        return self._payload(self._by_cik(cik).get("submissions"), "submissions")  # type: ignore[arg-type]

    def company_facts(self, cik: str) -> Payload:
        return self._payload(self._by_cik(cik).get("company_facts"), "company_facts")  # type: ignore[arg-type]

    def filing_exhibits(self, cik: str, accession: str) -> Payload:
        rec = self._by_cik(cik).get("exhibits", {}).get(accession)
        return self._payload(rec, "filing_exhibits")  # type: ignore[arg-type]

    def document_text(self, url: str) -> Payload:
        for p in sorted(self.dir.glob("*.json")):
            d = self._cache.get(p.stem) or json.loads(p.read_text())
            rec = d.get("documents", {}).get(url)
            if rec:
                return self._payload(rec, "document_text")
        raise NotFound("fixture", f"document not recorded: {url}")

    def earnings_history(self, ticker: str) -> Payload:
        return self._payload(self._load(ticker).get("earnings_history"), "earnings_history")  # type: ignore[arg-type]

    def prices(self, ticker: str, start: date, end: date) -> Payload:
        rec = None
        try:
            rec = self._load(ticker).get("prices")
        except NotFound:
            for f in sorted(self.dir.glob("*.json")):
                d = self._cache.get(f.stem) or json.loads(f.read_text())
                self._cache[f.stem] = d
                if d.get(f"prices_{ticker.upper()}"):
                    rec = d[f"prices_{ticker.upper()}"]
                    break
        p = self._payload(rec, "prices")  # type: ignore[arg-type]
        p.data = [b for b in p.data if start.isoformat() <= b["day"] <= end.isoformat()]
        return p

    def options_snapshot(self, ticker: str, event_date: date | None) -> Payload:
        return self._payload(self._load(ticker).get("options"), "options")  # type: ignore[arg-type]

    def short_interest(self, ticker: str) -> Payload:
        return self._payload(self._load(ticker).get("short_interest"), "short_interest")  # type: ignore[arg-type]

    def news(self, ticker: str, start: date, end: date) -> Payload:
        p = self._payload(self._load(ticker).get("news"), "news")  # type: ignore[arg-type]
        p.data = [n for n in p.data if not n.get("published") or str(n["published"])[:10] <= end.isoformat()]
        return p

    def form4(self, cik: str, accession: str, primary_doc: str) -> Payload:
        rec = self._by_cik(cik).get("form4", {}).get(accession)
        return self._payload(rec, "form4")  # type: ignore[arg-type]

    def earnings_calendar(self, start: date, end: date) -> Payload:
        rows: list[dict[str, Any]] = []
        for p in sorted(self.dir.glob("*.json")):
            d = json.loads(p.read_text())
            for r in d.get("earnings_history", {}).get("data", []):
                if r.get("date") and start.isoformat() <= r["date"] <= end.isoformat():
                    rows.append(
                        {
                            "symbol": p.stem,
                            "date": r["date"],
                            "epsEstimated": r.get("eps_est"),
                            "epsActual": r.get("eps_act"),
                            "revenueEstimated": r.get("rev_est"),
                            "revenueActual": r.get("rev_act"),
                        }
                    )
        return Payload(
            rows, SourceKind.FIXTURE, "Fixture earnings calendar", datetime(2026, 11, 20, tzinfo=UTC)
        )


class _Failing:
    """Wraps a provider and raises ProviderError for the named operations (eval outage simulation)."""

    def __init__(self, inner: Providers, fail: set[str]) -> None:
        self._inner, self._fail, self.name = inner, fail, inner.name

    def __getattr__(self, op: str) -> Any:
        fn = getattr(self._inner, op)
        if op in self._fail:

            def _down(*a: Any, **k: Any) -> Any:
                raise ProviderError(op, "simulated outage")

            return _down
        return fn


def get_providers(fixture: bool, fail: set[str] | None = None) -> Providers:
    p: Providers = FixtureProviders() if fixture else LiveProviders()
    return _Failing(p, fail) if fail else p  # type: ignore[return-value]
