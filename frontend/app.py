import streamlit as st
import os

st.set_page_config(
    page_title="Earnings Intelligence Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_URL = os.environ.get("API_URL", "http://backend:8000")

st.sidebar.title("📊 Earnings Intel")
st.sidebar.markdown("---")
st.sidebar.markdown("### Navigation")
st.sidebar.page_link("app.py", label="🏠 Dashboard", icon=None)
st.sidebar.page_link("pages/report_viewer.py", label="📋 Report Viewer", icon=None)
st.sidebar.page_link("pages/company_profile.py", label="🏢 Company Profile", icon=None)

st.title("Earnings Intelligence Platform")
st.markdown("**Multi-agent financial research powered by LangGraph, Snowflake Cortex, and Pinecone RAG**")

st.markdown("---")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Agents", "5", help="Pre-Earnings, Live, Transcript, Post-Earnings, Synthesis")
with col2:
    st.metric("Data Sources", "7", help="Snowflake, Pinecone, SEC EDGAR, FMP, Yahoo Finance, Tavily, OpenAI")
with col3:
    st.metric("Run Modes", "4", help="Full Cycle, Pre-Only, Post-Only, Question")

st.markdown("---")
st.subheader("Quick Research")

with st.form("quick_research"):
    col1, col2 = st.columns([3, 1])
    with col1:
        ticker = st.text_input("Ticker Symbol", value="NVDA", max_chars=10)
    with col2:
        mode = st.selectbox("Mode", ["full_cycle", "pre_only", "post_only", "question"])

    query = st.text_area(
        "Research Query",
        value="Analyze upcoming earnings expectations, historical beat rate, and options-implied move",
        height=80,
    )

    col1, col2 = st.columns(2)
    with col1:
        earnings_date = st.date_input("Earnings Date (optional)", value=None)
    with col2:
        async_mode = st.checkbox("Run in background", value=False)

    submitted = st.form_submit_button("Run Research", type="primary", use_container_width=True)

if submitted:
    import requests

    payload = {
        "ticker": ticker.upper(),
        "query": query,
        "mode": mode,
        "earnings_date": str(earnings_date) if earnings_date else None,
        "async_mode": async_mode,
    }

    with st.spinner(f"Running {mode} analysis for {ticker.upper()}..."):
        try:
            response = requests.post(f"{API_URL}/research", json=payload, timeout=300)
            response.raise_for_status()
            result = response.json()

            if result.get("status") == "running":
                st.info(f"Research queued. Run ID: {result['run_id']}")
                st.markdown("Check the **Report Viewer** page to track progress.")
            else:
                st.success(f"Research complete! Run ID: {result['run_id']}")
                st.session_state["latest_report"] = result

                if result.get("synthesis"):
                    st.subheader("Synthesis")
                    st.markdown(result["synthesis"]["response"])

                    with st.expander("Agent Details"):
                        for key in ["pre_earnings", "live_earnings", "transcript_analysis", "post_earnings"]:
                            agent_data = result.get(key)
                            if agent_data:
                                st.markdown(f"**{key.replace('_', ' ').title()}** (confidence: {agent_data['confidence']:.0%})")
                                st.markdown(agent_data["response"][:500] + "...")
                                st.markdown("---")
        except requests.exceptions.ConnectionError:
            st.error("Cannot connect to the API server. Make sure the backend is running.")
        except Exception as e:
            st.error(f"Error: {str(e)}")

st.markdown("---")
st.subheader("Watchlist")

try:
    import requests
    wl_response = requests.get(f"{API_URL}/watchlist", timeout=5)
    if wl_response.ok:
        watchlist = wl_response.json()
        if watchlist:
            for item in watchlist:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col1:
                    st.markdown(f"**{item['ticker']}**")
                with col2:
                    st.markdown(item.get("next_earnings", "—"))
                with col3:
                    if st.button("Remove", key=f"rm_{item['ticker']}"):
                        requests.delete(f"{API_URL}/watchlist/{item['ticker']}")
                        st.rerun()
        else:
            st.info("No tickers in watchlist. Add tickers from the Company Profile page.")
except Exception:
    st.info("Watchlist unavailable — backend not connected.")
