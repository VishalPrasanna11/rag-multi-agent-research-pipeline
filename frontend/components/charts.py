import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from typing import Dict, Any, List, Optional


def confidence_gauge(confidence: float, agent_name: str):
    color = "#2ecc71" if confidence >= 0.7 else "#f39c12" if confidence >= 0.4 else "#e74c3c"
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=confidence * 100,
        title={"text": agent_name},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 40], "color": "#fadbd8"},
                {"range": [40, 70], "color": "#fdebd0"},
                {"range": [70, 100], "color": "#d5f5e3"},
            ],
        },
        number={"suffix": "%"},
    ))
    fig.update_layout(height=200, margin={"t": 40, "b": 10, "l": 20, "r": 20})
    return fig


def earnings_surprise_chart(surprises: Dict[str, Any]):
    if not surprises:
        return None

    metrics = list(surprises.keys())
    actuals = [surprises[m]["actual"] for m in metrics]
    estimates = [surprises[m]["expected"] for m in metrics]
    surprise_pcts = [surprises[m]["surprise_pct"] for m in metrics]

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Estimate", x=metrics, y=estimates, marker_color="#3498db"))
    fig.add_trace(go.Bar(name="Actual", x=metrics, y=actuals, marker_color="#2ecc71"))

    for i, pct in enumerate(surprise_pcts):
        color = "#2ecc71" if pct > 0 else "#e74c3c"
        fig.add_annotation(
            x=metrics[i], y=max(actuals[i], estimates[i]),
            text=f"{pct:+.1f}%", showarrow=False,
            font={"color": color, "size": 14, "family": "Arial Black"},
            yshift=15,
        )

    fig.update_layout(
        barmode="group",
        title="Earnings Surprise",
        template="plotly_white",
        height=350,
    )
    return fig


def agent_timeline(agent_results: Dict[str, Any]):
    agents = []
    times = []
    confidences = []

    for name, result in agent_results.items():
        if result:
            agents.append(name.replace("_", " ").title())
            times.append(result.get("execution_time_ms", 0) / 1000)
            confidences.append(result.get("confidence", 0))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=agents,
        x=times,
        orientation="h",
        marker_color=[f"rgba(52, 152, 219, {c})" for c in confidences],
        text=[f"{t:.1f}s ({c:.0%})" for t, c in zip(times, confidences)],
        textposition="auto",
    ))
    fig.update_layout(
        title="Agent Execution Timeline",
        xaxis_title="Seconds",
        template="plotly_white",
        height=250,
    )
    return fig


def render_sources(sources: List[str]):
    if not sources:
        return
    st.markdown("**Sources:**")
    for s in sources[:10]:
        if s.startswith("http"):
            st.markdown(f"- [{s[:60]}...]({s})")
        else:
            st.markdown(f"- {s}")
