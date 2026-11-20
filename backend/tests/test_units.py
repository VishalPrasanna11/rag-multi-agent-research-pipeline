from datetime import UTC, date, datetime

import pytest

from app.facts import FactStore, LookAheadError, render, strip_tokens, uncited_numbers
from app.logging import redact
from app.market import classify_pattern, implied_move, quarterly_series, reaction, shrink, split_adjust
from app.schemas import SourceKind


# ---------------------------------------------------------------- redaction (F02)
@pytest.mark.parametrize(
    "raw",
    [
        "403 Client Error for url: https://x.com/api?symbol=NVDA&apikey=PjSiYuXfIQ2MiW",
        "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.abc.def",
        "openai error sk-proj-ABCDEFGHIJKLMNOP1234",
        "rediss://:p4ssw0rd@ec2-1-2-3.compute.amazonaws.com:6380",
        '{"api_key": "tvly-SECRETVALUE123"}',
    ],
)
def test_redact_removes_secrets(raw: str) -> None:
    out = redact(raw)
    for secret in (
        "PjSiYuXfIQ2MiW",
        "eyJhbGciOiJSUzI1NiJ9",
        "ABCDEFGHIJKLMNOP1234",
        "p4ssw0rd",
        "SECRETVALUE123",
    ):
        assert secret not in out


# ---------------------------------------------------------------- facts & grounding (F01, F11)
def _store() -> FactStore:
    return FactStore(datetime(2026, 11, 20, 23, 59, tzinfo=UTC))


def test_lookahead_guard_blocks_future_citation() -> None:
    st = _store()
    with pytest.raises(LookAheadError):
        st.cite(SourceKind.FMP, "future", datetime(2026, 11, 21, 12, tzinfo=UTC))


def test_render_and_uncited_numbers() -> None:
    st = _store()
    c = st.cite(SourceKind.FMP, "x", datetime(2026, 11, 1, tzinfo=UTC))
    st.add("consensus.eps_mean", "EPS", 1.234, "USD/share", c)
    md, used, unknown = render(
        "EPS consensus is {{fact:consensus.eps_mean}} in Q3 FY2027 per the 10-Q.", st.facts
    )
    assert used == ["consensus.eps_mean"] and not unknown
    assert "[[fact:consensus.eps_mean|$1.23]]" in md
    assert uncited_numbers(md) == []
    assert uncited_numbers("Revenue grew 23% to $5.1B.") != []
    assert strip_tokens(md).startswith("EPS consensus is $1.23")


def test_derive_uses_latest_input_as_of() -> None:
    st = _store()
    c1 = st.cite(SourceKind.FMP, "a", datetime(2026, 11, 1, tzinfo=UTC))
    c2 = st.cite(SourceKind.SEC_XBRL, "b", datetime(2026, 11, 10, tzinfo=UTC))
    st.add("a", "A", 2.0, "", c1)
    st.add("b", "B", 4.0, "", c2)
    f = st.derive("ratio", "A/B", 0.5, "x", ["a", "b"], "a/b")
    assert f is not None and f.as_of == datetime(2026, 11, 10, tzinfo=UTC)
    assert st.derive("missing", "M", 1.0, "x", ["a", "zzz"], "a*zzz") is None


# ---------------------------------------------------------------- market math (F12, F13, F14, F25)
BARS = [
    {"day": "2026-11-13", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
    {"day": "2026-11-16", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
    {"day": "2026-11-17", "open": 101, "high": 102, "low": 99, "close": 102, "volume": 1},
    {"day": "2026-11-18", "open": 110, "high": 112, "low": 104, "close": 108, "volume": 1},
    *[
        {"day": f"2026-11-{d}", "open": 108, "high": 109, "low": 107, "close": 109, "volume": 1}
        for d in range(19, 30)
    ],
]


def test_reaction_amc_uses_next_session() -> None:
    rx = reaction(BARS, date(2026, 11, 17), "amc")
    assert rx is not None and rx.pre_day == date(2026, 11, 17) and rx.post_day == date(2026, 11, 18)
    assert rx.day1_pct == pytest.approx((108 / 102 - 1) * 100)
    assert rx.gap_pct == pytest.approx((110 / 102 - 1) * 100)


def test_reaction_bmo_uses_same_session() -> None:
    rx = reaction(BARS, date(2026, 11, 17), "bmo")
    assert rx is not None and rx.pre_day == date(2026, 11, 16) and rx.post_day == date(2026, 11, 17)


def test_pattern_classification() -> None:
    assert classify_pattern(5, 6, 9) == "gap-and-go"
    assert classify_pattern(5, 6, 1) == "gap-and-fade"
    assert classify_pattern(-5, -6, -9) == "sell-and-extend"
    assert classify_pattern(0.5, 1.0, 1.0) == "muted"


def _cf(rows: list[dict]) -> dict:
    return {"facts": {"Revenues": {"unit": "USD", "values": rows}}}


def test_q4_derived_from_fy_minus_9m_and_fiscal_labels() -> None:
    rows = [
        {
            "start": "2025-01-27",
            "end": "2025-04-27",
            "val": 10,
            "fy": 2026,
            "fp": "Q1",
            "form": "10-Q",
            "filed": "2025-05-28",
            "accn": "a1",
        },
        {
            "start": "2025-04-28",
            "end": "2025-07-27",
            "val": 11,
            "fy": 2026,
            "fp": "Q2",
            "form": "10-Q",
            "filed": "2025-08-27",
            "accn": "a2",
        },
        {
            "start": "2025-07-28",
            "end": "2025-10-26",
            "val": 12,
            "fy": 2026,
            "fp": "Q3",
            "form": "10-Q",
            "filed": "2025-11-19",
            "accn": "a3",
        },
        {
            "start": "2025-01-27",
            "end": "2025-10-26",
            "val": 33,
            "fy": 2026,
            "fp": "Q3",
            "form": "10-Q",
            "filed": "2025-11-19",
            "accn": "a3",
        },
        {
            "start": "2025-01-27",
            "end": "2026-01-25",
            "val": 46,
            "fy": 2026,
            "fp": "FY",
            "form": "10-K",
            "filed": "2026-02-25",
            "accn": "k1",
        },
    ]
    s = quarterly_series(_cf(rows), ["Revenues"])
    assert [v.value for v in s] == [10, 11, 12, 13]
    assert s[-1].derived and s[-1].label == "FY2026 Q4"
    # as of before the 10-K was filed, Q4 must not exist (look-ahead)
    assert len(quarterly_series(_cf(rows), ["Revenues"], cutoff=date(2026, 2, 1))) == 3


def test_split_adjusts_values_filed_before_split() -> None:
    from app.market import QValue

    v = QValue(
        "EarningsPerShareDiluted",
        date(2024, 1, 1),
        date(2024, 3, 31),
        6.0,
        2024,
        "Q1",
        "10-Q",
        date(2024, 5, 1),
        "x",
    )
    bars = [{"day": "2024-06-10", "split": 10.0, "close": 1}]
    assert split_adjust([v], bars, per_share=True)[0].value == pytest.approx(0.6)


def test_implied_move_uses_first_expiry_after_event() -> None:
    chains = [
        {
            "expiry": "2026-11-20",
            "calls": [{"strike": 100, "bid": 1, "ask": 1}],
            "puts": [{"strike": 100, "bid": 1, "ask": 1}],
        },
        {
            "expiry": "2026-11-27",
            "calls": [{"strike": 100, "bid": 4, "ask": 4, "impliedVolatility": 0.6}],
            "puts": [{"strike": 100, "bid": 4, "ask": 4}],
        },
    ]
    im = implied_move(100.0, chains, date(2026, 11, 24), date(2026, 11, 19))
    assert im is not None and im["expiry"] == "2026-11-27" and im["implied_move_pct"] == pytest.approx(8.0)


def test_shrinkage_pulls_small_samples_toward_prior() -> None:
    assert shrink(1.0, 2) < 0.65
    assert shrink(None, 0) == 0.5
