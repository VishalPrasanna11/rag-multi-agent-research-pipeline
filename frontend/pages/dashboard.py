import streamlit as st
import requests
import os

API_URL = os.environ.get("API_URL", "http://backend:8000")

st.set_page_config(page_title="Dashboard", page_icon="📈", layout="wide")
st.title("📈 Research Dashboard")

try:
    health = requests.get(f"{API_URL}/health", timeout=5).json()
    status = health.get("status", "unknown")
    checks = health.get("checks", {})

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("API", "✅" if checks.get("api") else "❌")
    with col2:
        st.metric("Snowflake", "✅" if checks.get("snowflake") else "❌")
    with col3:
        st.metric("Pinecone", "✅" if checks.get("pinecone") else "❌")
    with col4:
        st.metric("Overall", status.title())
except Exception:
    st.warning("Backend not reachable.")

st.markdown("---")
st.subheader("Recent Reports")

try:
    reports = requests.get(f"{API_URL}/reports", params={"limit": 10}, timeout=10).json()
    if reports:
        for report in reports:
            with st.expander(f"{report['ticker']} — {report['status']} — {report['run_id'][:8]}"):
                st.markdown(f"**Query:** {report['query']}")
                st.markdown(f"**Status:** {report['status']}")
                if report.get("final_report"):
                    st.markdown(report["final_report"][:1000] + "...")
                col1, col2 = st.columns(2)
                with col1:
                    if report["status"] == "review":
                        if st.button("Approve", key=f"approve_{report['run_id']}"):
                            requests.post(
                                f"{API_URL}/research/{report['run_id']}/approve",
                                json={"approved": True},
                            )
                            st.rerun()
                with col2:
                    st.markdown(f"[View Full Report →](report_viewer?run_id={report['run_id']})")
    else:
        st.info("No reports yet. Run a research query from the home page.")
except Exception:
    st.info("Cannot load reports — backend not connected.")
