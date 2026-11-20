import streamlit as st
import requests
import os
import yfinance as yf
import plotly.graph_objects as go

API_URL = os.environ.get("API_URL", "http://backend:8000")

st.set_page_config(page_title="Company Profile", page_icon="🏢", layout="wide")
st.title("🏢 Company Profile")

ticker = st.text_input("Ticker Symbol", value="NVDA").upper()

if not ticker:
    st.stop()

try:
    stock = yf.Ticker(ticker)
    info = stock.info

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Price", f"${info.get('currentPrice', 'N/A')}")
    with col2:
        st.metric("Market Cap", f"${info.get('marketCap', 0)/1e9:.1f}B")
    with col3:
        st.metric("P/E Ratio", f"{info.get('trailingPE', 'N/A'):.1f}" if info.get('trailingPE') else "N/A")
    with col4:
        st.metric("52W Range", f"${info.get('fiftyTwoWeekLow', 'N/A')} - ${info.get('fiftyTwoWeekHigh', 'N/A')}")

    st.markdown("---")

    hist = stock.history(period="6mo")
    if not hist.empty:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=hist.index,
            open=hist["Open"],
            high=hist["High"],
            low=hist["Low"],
            close=hist["Close"],
            name="Price",
        ))
        fig.update_layout(
            title=f"{ticker} — 6 Month Price History",
            template="plotly_white",
            height=400,
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("Company Info")
    st.markdown(f"**{info.get('longName', ticker)}**")
    st.markdown(info.get("longBusinessSummary", "No description available.")[:500])

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Key Financials")
        financials = {
            "Revenue (TTM)": f"${info.get('totalRevenue', 0)/1e9:.1f}B" if info.get('totalRevenue') else "N/A",
            "Gross Margin": f"{info.get('grossMargins', 0)*100:.1f}%" if info.get('grossMargins') else "N/A",
            "Operating Margin": f"{info.get('operatingMargins', 0)*100:.1f}%" if info.get('operatingMargins') else "N/A",
            "Net Margin": f"{info.get('profitMargins', 0)*100:.1f}%" if info.get('profitMargins') else "N/A",
            "EPS (TTM)": f"${info.get('trailingEps', 'N/A')}",
            "Forward P/E": f"{info.get('forwardPE', 'N/A'):.1f}" if info.get('forwardPE') else "N/A",
        }
        for label, val in financials.items():
            st.markdown(f"**{label}:** {val}")

    with col2:
        st.subheader("Quick Actions")
        if st.button("Run Full Analysis", type="primary", use_container_width=True):
            st.switch_page("app.py")

        if st.button("Add to Watchlist", use_container_width=True):
            try:
                resp = requests.post(f"{API_URL}/watchlist", json={"ticker": ticker}, timeout=5)
                if resp.ok:
                    st.success(f"{ticker} added to watchlist!")
                elif resp.status_code == 409:
                    st.info(f"{ticker} already in watchlist.")
                else:
                    st.error("Failed to add to watchlist.")
            except Exception:
                st.warning("Backend not available.")

except Exception as e:
    st.error(f"Could not load data for {ticker}: {e}")
