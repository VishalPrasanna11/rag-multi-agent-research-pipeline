import streamlit as st
import requests
import os
from components.charts import confidence_gauge, earnings_surprise_chart, agent_timeline, render_sources

API_URL = os.environ.get("API_URL", "http://backend:8000")

st.set_page_config(page_title="Report Viewer", page_icon="📋", layout="wide")
st.title("📋 Report Viewer")

run_id = st.query_params.get("run_id", "")
if not run_id:
    run_id = st.text_input("Enter Run ID")

if not run_id:
    st.info("Enter a run ID or click a report link from the dashboard.")
    st.stop()

try:
    response = requests.get(f"{API_URL}/research/{run_id}", timeout=30)
    response.raise_for_status()
    report = response.json()
except Exception as e:
    st.error(f"Failed to load report: {e}")
    st.stop()

st.markdown(f"### {report['ticker']} — {report['status'].upper()}")
st.markdown(f"**Query:** {report['query']}")

st.markdown("---")

if report.get("synthesis"):
    st.subheader("Synthesis Report")
    st.markdown(report["synthesis"]["response"])

    col1, col2 = st.columns([1, 3])
    with col1:
        fig = confidence_gauge(report["synthesis"]["confidence"], "Synthesis")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        render_sources(report["synthesis"].get("sources", []))

st.markdown("---")

agent_results = {}
for key in ["pre_earnings", "live_earnings", "transcript_analysis", "post_earnings"]:
    if report.get(key):
        agent_results[key] = report[key]

if agent_results:
    st.subheader("Agent Execution")
    fig = agent_timeline(agent_results)
    st.plotly_chart(fig, use_container_width=True)

tabs = st.tabs(["Pre-Earnings", "Live Earnings", "Transcript", "Post-Earnings"])

for tab, (key, label) in zip(tabs, [
    ("pre_earnings", "Pre-Earnings"),
    ("live_earnings", "Live Earnings"),
    ("transcript_analysis", "Transcript Analysis"),
    ("post_earnings", "Post-Earnings"),
]):
    with tab:
        agent_data = report.get(key)
        if agent_data:
            st.markdown(agent_data["response"])

            col1, col2 = st.columns([1, 3])
            with col1:
                fig = confidence_gauge(agent_data["confidence"], label)
                st.plotly_chart(fig, use_container_width=True)
            with col2:
                structured = agent_data.get("structured_data", {})
                if "surprises" in structured:
                    fig = earnings_surprise_chart(structured["surprises"])
                    if fig:
                        st.plotly_chart(fig, use_container_width=True)

            render_sources(agent_data.get("sources", []))
            st.markdown(f"*Execution time: {agent_data.get('execution_time_ms', 0)}ms*")
        else:
            st.info(f"{label} agent was not run in this analysis.")

st.markdown("---")

if report.get("final_report"):
    with st.expander("Full Final Report"):
        st.markdown(report["final_report"])

if report["status"] == "review":
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Approve Report", type="primary", use_container_width=True):
            requests.post(f"{API_URL}/research/{run_id}/approve", json={"approved": True})
            st.success("Report approved!")
            st.rerun()
    with col2:
        if st.button("❌ Reject Report", use_container_width=True):
            requests.post(f"{API_URL}/research/{run_id}/approve", json={"approved": False})
            st.warning("Report rejected.")
            st.rerun()
