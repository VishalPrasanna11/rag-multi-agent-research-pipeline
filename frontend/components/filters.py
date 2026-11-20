import streamlit as st
from typing import Tuple, Optional, List


def ticker_input(default: str = "NVDA") -> str:
    return st.text_input("Ticker", value=default, max_chars=10).upper()


def date_range_filter() -> Tuple[Optional[str], Optional[str]]:
    col1, col2 = st.columns(2)
    with col1:
        start = st.date_input("Start Date", value=None)
    with col2:
        end = st.date_input("End Date", value=None)
    return (str(start) if start else None, str(end) if end else None)


def mode_selector() -> str:
    return st.selectbox(
        "Analysis Mode",
        ["full_cycle", "pre_only", "post_only", "question"],
        format_func=lambda x: {
            "full_cycle": "Full Earnings Cycle",
            "pre_only": "Pre-Earnings Only",
            "post_only": "Post-Earnings Only",
            "question": "Ad-hoc Question",
        }.get(x, x),
    )


def agent_selector() -> List[str]:
    agents = st.multiselect(
        "Active Agents",
        ["pre_earnings_node", "live_earnings_node", "transcript_node", "post_earnings_node"],
        default=["pre_earnings_node", "live_earnings_node", "transcript_node", "post_earnings_node"],
        format_func=lambda x: x.replace("_node", "").replace("_", " ").title(),
    )
    return agents
