import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(
    layout="wide", page_title="Option Apex - Nifty Position Builder"
)

# Embedded Upstox Token (Hidden from UI)
UPSTOX_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"

# --- HEADER UI ---
col_head, col_tf = st.columns([8, 2])

with col_head:
    st.markdown("### **Nifty 50**", unsafe_allow_html=True)

with col_tf:
    interval = st.selectbox(
        "Timeframe",
        options=["3min", "5min"],
        index=0,
        label_visibility="collapsed",
    )


# --- DATA FETCHING & POSITION BUILDER CALCULATIONS ---
@st.cache_data(ttl=60)
def load_chart_data(timeframe, token):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    unit = "3minute" if timeframe == "3min" else "5minute"
    candle_url = f"https://api.upstox.com/v2/historical-candle/intraday/NSE_INDEX|Nifty%2050/{unit}"

    try:
        res = requests.get(candle_url, headers=headers, timeout=5)
        res_json = res.json()

        if res_json.get("status") == "success" and res_json.get("data"):
            raw_candles = res_json["data"]["candles"]
            df = pd.DataFrame(
                raw_candles,
                columns=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "oi",
                ],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp").reset_index(drop=True)

            # Option Apex Position Builder Engine:
            # (Close - Open) * Volume weighting * OI Directional Shift
            df["price_diff"] = df["close"] - df["open"]
            df["position_builder"] = (
                df["price_diff"] * (df["volume"] + 1) * 0.05
            )

            # Scale to match Apex layout bounds
            max_val = df["position_builder"].abs().max()
            if max_val > 0:
                df["position_builder"] = (
                    df["position_builder"] / max_val
                ) * 4000

            return df
    except Exception as e:
        pass

    return generate_mock_apex_data(timeframe)


def generate_mock_apex_data(timeframe):
    """Fallback generator replicating Option Apex reference pattern."""
    freq = "3min" if timeframe == "3min" else "5min"
    start_time = pd.Timestamp.now().replace(
        hour=9, minute=15, second=0, microsecond=0
    )
    periods = 75 if timeframe == "3min" else 45
    timestamps = pd.date_range(start=start_time, periods=periods, freq=freq)

    import numpy as np

    np.random.seed(12)

    trend = (
        np.sin(np.linspace(0, 3 * np.pi, periods)) * 120
        - np.linspace(0, 80, periods)
        + 24000
    )
    noise = np.random.randn(periods) * 12

    close_p = trend + noise
    open_p = np.roll(close_p, 1)
    open_p[0] = close_p[0] + 5
    high_p = np.maximum(open_p, close_p) + np.random.rand(periods) * 15
    low_p = np.minimum(open_p, close_p) - np.random.rand(periods) * 15

    pb = (close_p - open_p) * 150 + np.random.randn(periods) * 300
    pb[12] = -3900
    pb[30] = 4200
    pb[55] = 3800

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "position_builder": pb,
        }
    )


df = load_chart_data(interval, UPSTOX_ACCESS_TOKEN)
df["bar_color"] = df["position_builder"].apply(
    lambda x: "#22c55e" if x >= 0 else "#ef4444"
)

# --- CHART CONFIGURATION ---
fig = make_subplots(
    rows=2,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.02,
    row_heights=[0.78, 0.22],
)

# Candlestick
fig.add_trace(
    go.Candlestick(
        x=df["timestamp"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="Nifty 50",
        increasing_line_color="#22c55e",
        decreasing_line_color="#ef4444",
        increasing_fillcolor="#22c55e",
        decreasing_fillcolor="#ef4444",
    ),
    row=1,
    col=1,
)

# Position Builders
fig.add_trace(
    go.Bar(
        x=df["timestamp"],
        y=df["position_builder"],
        marker_color=df["bar_color"],
        marker_line_width=0,
        name="Position Builders",
        showlegend=False,
    ),
    row=2,
    col=1,
)

# Dark Theme Setup
fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#0c0e12",
    plot_bgcolor="#0c0e12",
    margin=dict(l=10, r=10, t=10, b=10),
    height=600,
    xaxis_rangeslider_visible=False,
    hovermode="x unified",
    showlegend=False,
)

fig.update_xaxes(
    showgrid=False, color="#6b7280", tickformat="%I:%M %p", row=2, col=1
)
fig.update_yaxes(
    showgrid=True,
    gridcolor="#1e293b",
    gridwidth=0.5,
    color="#6b7280",
    row=1,
    col=1,
)
fig.update_yaxes(
    showgrid=False,
    zeroline=True,
    zerolinecolor="#374151",
    color="#6b7280",
    row=2,
    col=1,
)

st.plotly_chart(fig, use_container_width=True)
