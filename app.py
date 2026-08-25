import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Nifty 50 Position Builder Chart")


# 1. Upstox API Data Fetcher
@st.cache_data(ttl=60)
def fetch_upstox_candles(instrument_key, interval, access_token=""):
    """Fetches intraday candlestick data from Upstox API."""
    # Convert timeframe interval key for Upstox API (3minute, 5minute)
    unit = "3minute" if interval == "3min" else "5minute"
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/{unit}"

    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        res_data = response.json()
        if res_data.get("status") == "success" and res_data.get("data"):
            candles = res_data["data"]["candles"]
            # Upstox returns: [timestamp, open, high, low, close, volume, open_interest]
            df = pd.DataFrame(
                candles,
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
            return df
    except Exception as e:
        st.error(f"Error fetching data from Upstox: {e}")

    # Fallback/Mock data generator matching the image structure if API token isn't provided
    return generate_mock_data(interval)


def generate_mock_data(interval):
    """Generates synthetic intraday data with simulated position builder values."""
    freq = "3min" if interval == "3min" else "5min"
    start_time = pd.Timestamp.now().replace(
        hour=9, minute=15, second=0, microsecond=0
    )
    periods = 75 if interval == "3min" else 45
    timestamps = pd.date_range(start=start_time, periods=periods, freq=freq)

    import numpy as np

    np.random.seed(42)

    close_prices = 24000 + np.cumsum(np.random.randn(periods) * 15)
    open_prices = close_prices + np.random.randn(periods) * 5
    high_prices = (
        np.maximum(open_prices, close_prices) + np.random.rand(periods) * 10
    )
    low_prices = (
        np.minimum(open_prices, close_prices) - np.random.rand(periods) * 10
    )

    # Simulate Option Apex "Position Builders" (Net multi-leg buying/selling interest)
    position_builders = np.random.randn(periods) * 1500
    # Add occasional position spikes (matching the image)
    position_builders[10] = -3800
    position_builders[25] = 4200
    position_builders[35] = 3900

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_prices,
            "high": high_prices,
            "low": low_prices,
            "close": close_prices,
            "position_builder": position_builders,
        }
    )


# 2. UI Header Controls
col_title, col_timeframe, col_token = st.columns([3, 2, 3])

with col_title:
    st.markdown(
        "### **Nifty 50** <span style='color:#3b82f6; font-size: 14px;'>How to use 💡</span>",
        unsafe_allow_html=True,
    )

with col_timeframe:
    timeframe = st.selectbox(
        "Timeframe",
        options=["3min", "5min"],
        index=0,
        label_visibility="collapsed",
    )

with col_token:
    access_token = st.text_input(
        "Upstox Access Token (Optional)",
        type="password",
        placeholder="eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU",
        label_visibility="collapsed",
    )

# 3. Data Loading
instrument_key = "NSE_INDEX|Nifty 50"
df = fetch_upstox_candles(instrument_key, timeframe, access_token)

# Calculate Position Builder fallback if using real Upstox volume/OI data
if "position_builder" not in df.columns:
    # Basic proxy calculation when live API volume/OI change is used
    df["price_change"] = df["close"].diff().fillna(0)
    df["position_builder"] = (
        df["volume"] * (df["price_change"] / df["close"]) * 100
    )

# Colors matching Option Apex dark theme
df["bar_color"] = df["position_builder"].apply(
    lambda x: "#10b981" if x >= 0 else "#ef4444"
)

# 4. Plotly Subplot Figure Construction
fig = make_subplots(
    rows=2,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=[0.75, 0.25],
)

# Top Subplot: Candlestick Chart
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

# Bottom Subplot: Position Builders Bar Chart
fig.add_trace(
    go.Bar(
        x=df["timestamp"],
        y=df["position_builder"],
        marker_color=df["bar_color"],
        name="Position Builders",
        showlegend=False,
    ),
    row=2,
    col=1,
)

# Styling layout to match Option Apex Dark UI
fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#11161d",
    plot_bgcolor="#11161d",
    margin=dict(l=10, r=10, t=10, b=10),
    height=580,
    xaxis_rangeslider_visible=False,
    hovermode="x unified",
    showlegend=False,
)

# Axis styling
fig.update_xaxes(
    showgrid=False,
    color="#9ca3af",
    tickformat="%I:%M %p",
    row=2,
    col=1,
)
fig.update_yaxes(
    showgrid=True,
    gridcolor="#1f2937",
    color="#9ca3af",
    row=1,
    col=1,
)
fig.update_yaxes(
    showgrid=True,
    gridcolor="#1f2937",
    zeroline=True,
    zerolinecolor="#4b5563",
    color="#9ca3af",
    row=2,
    col=1,
)

# 5. Render Chart
st.plotly_chart(fig, use_container_width=True)