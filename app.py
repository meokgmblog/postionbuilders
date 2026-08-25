from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Options Apex - Realtime")

UPSTOX_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNxA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"


# ==========================================
# DATA FETCHING (STRICT LIVE TIMESTAMP LIMIT)
# ==========================================
def fetch_upstox_ohlc(instrument_key, interval="3minute", api_token=""):
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/{interval}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_token}",
    }

    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", {}).get("candles", [])
            if data:
                df = pd.DataFrame(
                    data,
                    columns=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "vol",
                        "oi",
                    ],
                )
                df["timestamp"] = pd.to_datetime(df["timestamp"])

                # Filter out any future timestamps strictly against current execution time
                now = pd.Timestamp.now()
                df = df[df["timestamp"] <= now]

                return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()


def generate_mock_apex_data(timeframe):
    """Generates synthetic data strictly bounded between 9:15 AM and current system time."""
    freq = "3min" if timeframe == "3min" else "5min"
    now = pd.Timestamp.now()
    today_start = now.replace(hour=9, minute=15, second=0, microsecond=0)

    # Cap maximum range strictly to current time or market close 3:30 PM (whichever is earlier)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    end_time = min(now, market_close)

    if end_time < today_start:
        end_time = today_start.replace(hour=15, minute=30) - pd.Timedelta(
            days=1
        )
        today_start = end_time.replace(hour=9, minute=15)

    timestamps = pd.date_range(start=today_start, end=end_time, freq=freq)
    n = len(timestamps)

    if n == 0:
        return pd.DataFrame()

    import numpy as np

    np.random.seed(42)

    t = np.linspace(0, 3 * np.pi, n)
    base_wave = np.sin(t) * 90 - (t * 10)

    close_p = 24000 + base_wave + np.random.randn(n) * 5
    open_p = np.roll(close_p, 1)
    open_p[0] = close_p[0] - 5
    high_p = np.maximum(open_p, close_p) + np.random.rand(n) * 8
    low_p = np.minimum(open_p, close_p) - np.random.rand(n) * 8

    pb = (close_p - open_p) * 80 + np.random.randn(n) * 100
    if n > 15:
        pb[int(n * 0.45)] = 4100

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "pos_builder": pb,
        }
    )
    df["bar_color"] = df["pos_builder"].apply(
        lambda x: "#10b981" if x >= 0 else "#ef4444"
    )
    return df


def load_data(timeframe):
    interval_unit = "3minute" if timeframe == "3min" else "5minute"
    df = fetch_upstox_ohlc(
        "NSE_INDEX|Nifty 50", interval=interval_unit, api_token=UPSTOX_ACCESS_TOKEN
    )

    if df.empty:
        df = generate_mock_apex_data(timeframe)
    else:
        # Calculate Position Builders from Price variance and Volume
        df["price_diff"] = df["close"] - df["open"]
        df["pos_builder"] = df["price_diff"] * (df["vol"] ** 0.5) * 2.5
        df["bar_color"] = df["pos_builder"].apply(
            lambda x: "#10b981" if x >= 0 else "#ef4444"
        )

    return df


# ==========================================
# UI CONTROLS
# ==========================================
col_head, col_tf = st.columns([8, 2])

with col_head:
    st.markdown("### **Nifty 50**", unsafe_allow_html=True)

with col_tf:
    timeframe = st.selectbox(
        "Timeframe",
        options=["3min", "5min"],
        index=0,
        label_visibility="collapsed",
    )

df = load_data(timeframe)

# ==========================================
# PLOTLY CANVAS
# ==========================================
fig = make_subplots(
    rows=2,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.01,
    row_heights=[0.78, 0.22],
)

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

fig.add_trace(
    go.Bar(
        x=df["timestamp"],
        y=df["pos_builder"],
        marker_color=df["bar_color"],
        marker_line_width=0,
        name="Position Builders",
        showlegend=False,
    ),
    row=2,
    col=1,
)

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#0c0e12",
    plot_bgcolor="#0c0e12",
    margin=dict(l=5, r=5, t=5, b=5),
    height=600,
    xaxis_rangeslider_visible=False,
    hovermode="x unified",
    showlegend=False,
)

fig.update_xaxes(
    showgrid=False,
    color="#4b5563",
    tickformat="%I:%M %p",
    # Set x-axis limit up to the latest bar available, preventing empty space to 15:30
    range=[
        df["timestamp"].min() - pd.Timedelta(minutes=5),
        df["timestamp"].max() + pd.Timedelta(minutes=5),
    ],
    row=2,
    col=1,
)

fig.update_yaxes(
    showgrid=True,
    gridcolor="#111827",
    color="#4b5563",
    side="right",
    row=1,
    col=1,
)

fig.update_yaxes(
    showgrid=False,
    zeroline=True,
    zerolinecolor="#374151",
    showticklabels=False,
    row=2,
    col=1,
)

st.plotly_chart(fig, use_container_width=True)
