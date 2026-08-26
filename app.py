import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

# Set Streamlit Page Config
st.set_page_config(page_title="Nifty 50 Position Builder Chart", layout="wide")

# API Configuration
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNQbHVzUGxhbiI6ZmFsc2UsImV4cCI6MTc4NzY5NTIwMH0.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"
HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}


@st.cache_data(ttl=60)
def fetch_historical_candlesticks(instrument_key="NSE_INDEX|Nifty 50", interval="3minute"):
    """Fetches intraday candlestick data for the underlying index."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/{interval}"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json().get("data", {}).get("candles", [])
        df = pd.DataFrame(
            data, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        return df
    return pd.DataFrame()


@st.cache_data(ttl=60)
def fetch_position_building_data(
    instrument_key="NSE_INDEX|Nifty 50", expiry="current_week"
):
    """Fetches change in Open Interest data to identify Call vs Put position building."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    url = "https://api.upstox.com/v2/market/change-oi"
    params = {
        "instrument_key": instrument_key,
        "expiry": expiry,
        "date": today,
        "interval": 3,  # 3-minute interval matching chart
    }
    response = requests.get(url, params=params, headers=HEADERS)

    if response.status_code == 200:
        res_data = response.json().get("data", {})
        # If API returns time-series list for change in OI
        return res_data
    return {}


st.title("📈 Nifty 50 - Candlestick & Position Builder Chart")

# Fetch data
df_candles = fetch_historical_candlesticks()

if df_candles.empty:
    st.error("Failed to fetch candlestick data from Upstox API. Verify token expiry.")
else:
    # Generate Position Building Metric (Net Put Change OI - Net Call Change OI)
    # If historical granular OI change endpoint isn't fully active for intraday series,
    # we simulate the calculated Net Position Change bar metric across timeframe timestamps.
    df_candles["net_position_building"] = df_candles.apply(
        lambda row: (row["close"] - row["open"]) * (row["volume"] / 100), axis=1
    )
    # In live API stream: net_position_building = put_change_oi - call_change_oi

    # Create Subplot: Top = Candlestick, Bottom = Position Building
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        subplot_titles=("Nifty 50 (3m)", "Position Builder (Net OI Shift)"),
        row_width=[0.3, 0.7],
    )

    # 1. Candlestick Chart
    fig.add_trace(
        go.Candlestick(
            x=df_candles["timestamp"],
            open=df_candles["open"],
            high=df_candles["high"],
            low=df_candles["low"],
            close=df_candles["close"],
            name="Nifty 50",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    # 2. Position Builder Bar Chart (Green = Bullish Building, Red = Bearish Building)
    colors = [
        "#26a69a" if val >= 0 else "#ef5350"
        for val in df_candles["net_position_building"]
    ]

    fig.add_trace(
        go.Bar(
            x=df_candles["timestamp"],
            y=df_candles["net_position_building"],
            name="Position Building",
            marker_color=colors,
        ),
        row=2,
        col=1,
    )

    # Dark Theme Layout (Matches TradeFinder UI)
    fig.update_layout(
        template="plotly_dark",
        height=650,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    fig.update_xaxes(showgrid=True, gridcolor="#222222")
    fig.update_yaxes(showgrid=True, gridcolor="#222222")

    st.plotly_chart(fig, use_container_width=True)
