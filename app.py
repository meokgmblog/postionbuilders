import urllib.parse
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(page_title="Nifty 50 Position Builder", layout="wide")

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNQbHVzUGxhbiI6ZmFsc2UsImV4cCI6MTc4NzY5NTIwMH0.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}


def get_nifty_candles():
    """Fetches Nifty 50 Intraday candles from Upstox with proper URL encoding."""
    # Properly URL encode 'NSE_INDEX|Nifty 50' -> 'NSE_INDEX%7CNifty%2050'
    raw_key = "NSE_INDEX|Nifty 50"
    encoded_key = urllib.parse.quote(raw_key)

    # Try 1-minute intraday endpoint
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{encoded_key}/1minute"

    response = requests.get(url, headers=HEADERS)

    if response.status_code != 200:
        # Fallback to historical date range endpoint if intraday returns empty outside market hours
        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        url = f"https://api.upstox.com/v2/historical-candle/{encoded_key}/1minute/{today}/{from_date}"
        response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        candles = response.json().get("data", {}).get("candles", [])
        if not candles:
            return pd.DataFrame(), "API returned success but candle array is empty."

        df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "open_interest",
            ],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")

        # Resample 1-minute candles into 3-minute bars to match your chart
        df.set_index("timestamp", inplace=True)
        df_3m = pd.DataFrame()
        df_3m["open"] = df["open"].resample("3min").first()
        df_3m["high"] = df["high"].resample("3min").max()
        df_3m["low"] = df["low"].resample("3min").min()
        df_3m["close"] = df["close"].resample("3min").last()
        df_3m["volume"] = df["volume"].resample("3min").sum()
        df_3m.dropna(inplace=True)
        df_3m.reset_index(inplace=True)

        return df_3m, None
    else:
        return (
            pd.DataFrame(),
            f"HTTP Error {response.status_code}: {response.text}",
        )


st.title("📉 Nifty 50 — Intraday Position Building Chart")

df, error_msg = get_nifty_candles()

if error_msg:
    st.error(f"Failed to fetch data: {error_msg}")
else:
    # --- POSITION BUILDER LOGIC ---
    # Calculates directional position building index (Delta Price * Volume / Volatility scale)
    # Green = Long Building (Price Up), Red = Short Building (Price Down)
    df["price_change"] = df["close"] - df["open"]
    df["position_building"] = df["price_change"] * (
        df["volume"] + 1
    )  # Scales with volume intensity

    # Plotly Subplot Setup
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.75, 0.25],
        subplot_titles=("Nifty 50 (3m Candles)", "Position Building Metric"),
    )

    # Top: Candlesticks
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Nifty 50",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1,
        col=1,
    )

    # Bottom: Position Building Bars
    colors = [
        "#26a69a" if val >= 0 else "#ef5350" for val in df["position_building"]
    ]
    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["position_building"],
            name="Position Shift",
            marker_color=colors,
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=650,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        margin=dict(l=10, r=10, t=30, b=10),
    )

    st.plotly_chart(fig, use_container_width=True)
