from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Live Nifty 50 Chart")

# ==========================================
# 1. UPSTOX LIVE DATA FETCHING
# ==========================================
def fetch_live_upstox_ohlc(instrument_key, interval, api_token):
    """Fetches actual live intraday OHLC candles directly from Upstox API."""
    # Convert Streamlit timeframe label to Upstox API format
    upstox_interval = "1minute" if interval == "1min" else ("3minute" if interval == "3min" else "5minute")
    
    # Endpoint for today's intraday data
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/{upstox_interval}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_token}",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            res_data = response.json()
            candles = res_data.get("data", {}).get("candles", [])
            
            if not candles:
                st.error("Upstox returned empty candle data. Check if market is active or token is valid.")
                return pd.DataFrame()

            # Upstox returns: [timestamp, open, high, low, close, volume, open_interest]
            df = pd.DataFrame(
                candles,
                columns=["timestamp", "open", "high", "low", "close", "vol", "oi"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            
            # Sort chronologically (Upstox returns newest first)
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df
        else:
            st.error(f"Upstox API Error [{response.status_code}]: {response.text}")
            return pd.DataFrame()
            
    except Exception as e:
        st.error(f"Connection Error: {str(e)}")
        return pd.DataFrame()

# ==========================================
# 2. STREAMLIT UI CONTROLS
# ==========================================
st.title("Nifty 50 - Realtime Market Data")

col_token, col_tf, col_btn = st.columns([6, 2, 2])

with col_token:
    api_token = st.text_input("Upstox Bearer Access Token", type="password")

with col_tf:
    timeframe = st.selectbox("Timeframe", options=["1min", "3min", "5min"], index=2)

with col_btn:
    st.write("")
    st.write("")
    refresh = st.button("Refresh Live Chart")

# Instrument key for Nifty 50 Index
SPOT_KEY = "NSE_INDEX|Nifty 50"

if api_token:
    df = fetch_live_upstox_ohlc(SPOT_KEY, timeframe, api_token)

    if not df.empty:
        # Calculate Delta Changes for Histogram (Position Indicators)
        df["close_diff"] = df["close"] - df["open"]
        df["bar_color"] = df["close_diff"].apply(lambda x: "#10b981" if x >= 0 else "#ef4444")

        # ==========================================
        # 3. PLOTLY CHART ENGINE
        # ==========================================
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.02,
            row_heights=[0.75, 0.25],
        )

        # Candlesticks
        fig.add_trace(
            go.Candlestick(
                x=df["timestamp"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="Nifty 50",
                increasing_line_color="#10b981",
                decreasing_line_color="#ef4444",
                increasing_fillcolor="#10b981",
                decreasing_fillcolor="#ef4444",
            ),
            row=1,
            col=1,
        )

        # Volume / Bar Indicator
        fig.add_trace(
            go.Bar(
                x=df["timestamp"],
                y=df["vol"] if df["vol"].sum() > 0 else df["close_diff"].abs(),
                marker_color=df["bar_color"],
                name="Volume / Momentum",
                showlegend=False,
            ),
            row=2,
            col=1,
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0c0e12",
            plot_bgcolor="#0c0e12",
            margin=dict(l=10, r=50, t=10, b=10),
            height=650,
            xaxis_rangeslider_visible=False,
            hovermode="x unified",
            showlegend=False,
        )

        fig.update_xaxes(
            showgrid=True,
            gridcolor="#1f2937",
            color="#9ca3af",
            tickformat="%H:%M",
            row=2,
            col=1,
        )

        fig.update_yaxes(
            showgrid=True,
            gridcolor="#1f2937",
            color="#9ca3af",
            side="right",
            row=1,
            col=1,
        )

        fig.update_yaxes(
            showgrid=False,
            side="right",
            row=2,
            col=1,
        )

        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Please enter your active Upstox Access Token above to fetch live market candles.")
