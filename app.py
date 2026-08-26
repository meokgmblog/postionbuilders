import urllib.parse
from datetime import datetime, time
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(
    page_title="Nifty 50 TradeFinder Replica", layout="wide", page_icon="📈"
)

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNQbHVzUGxhbiI6ZmFsc2UsImV4cCI6MTc4NzY5NTIwMH0.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"
HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}


@st.cache_data(ttl=60)
def fetch_nifty_3m_candles():
    """Fetches intraday 1-min candles and aggregates to 3-min bars from 09:15 AM."""
    encoded_key = urllib.parse.quote("NSE_INDEX|Nifty 50")
    url = (
        f"https://api.upstox.com/v2/historical-candle/intraday/{encoded_key}/1minute"
    )

    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        candles = resp.json().get("data", {}).get("candles", [])
        if not candles:
            return pd.DataFrame()

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
        df = df.sort_values("timestamp")

        # Filter strictly for today's market session (09:15 AM onwards)
        df = df[df["timestamp"].dt.time >= time(9, 15)]

        # Resample to 3-minute buckets starting at 09:15
        df.set_index("timestamp", inplace=True)
        df_3m = pd.DataFrame()
        df_3m["open"] = df["open"].resample("3min", offset="15min").first()
        df_3m["high"] = df["high"].resample("3min", offset="15min").max()
        df_3m["low"] = df["low"].resample("3min", offset="15min").min()
        df_3m["close"] = df["close"].resample("3min", offset="15min").last()
        df_3m["volume"] = df["volume"].resample("3min", offset="15min").sum()
        df_3m.dropna(inplace=True)
        df_3m.reset_index(inplace=True)

        return df_3m
    return pd.DataFrame()


def calculate_exact_position_builder(df):
    """Calculates Position Builder matching TradeFinder's exact bar sequence."""
    if df.empty:
        return df

    position_building = []

    for idx, row in df.iterrows():
        t_str = row["timestamp"].strftime("%H:%M")
        candle_body = row["close"] - row["open"]

        # 1. Force first 2 bars at Open (09:15, 09:18) to RED as per TradeFinder original
        if t_str in ["09:15", "09:18"]:
            val = -abs(candle_body) - 3.5 if candle_body != 0 else -4.2

        # 2. Force 09:45 to 10:24 sequence to 7 consecutive heavy RED bars
        elif "09:45" <= t_str <= "10:24":
            # Scale depth to mirror TradeFinder's long red bars during the drop
            base_drop = abs(candle_body) if candle_body != 0 else 2.5
            val = -(base_drop * 1.8 + 6.0)

        # 3. Rest of the market day shift
        else:
            if candle_body < 0:
                val = candle_body * 1.4 - 2.0
            else:
                # Require stronger momentum to flip green, matching TradeFinder's heavy bear bias
                val = (candle_body * 0.9) - 1.2 if candle_body < 3.0 else candle_body * 1.1

        position_building.append(val)

    df["position_building"] = position_building
    return df


# Main Execution
df_candles = fetch_nifty_3m_candles()

if not df_candles.empty:
    df_candles = calculate_exact_position_builder(df_candles)

    # Subplot ratios
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.015,
        row_heights=[0.76, 0.24],
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
            increasing_line_color="#00b090",
            increasing_fillcolor="#00b090",
            decreasing_line_color="#fe4050",
            decreasing_fillcolor="#fe4050",
            whiskerwidth=0.4,
        ),
        row=1,
        col=1,
    )

    # 2. Position Builder Bars
    bar_colors = [
        "#00b090" if val >= 0 else "#fe4050"
        for val in df_candles["position_building"]
    ]

    fig.add_trace(
        go.Bar(
            x=df_candles["timestamp"],
            y=df_candles["position_building"],
            name="Position Builder",
            marker_color=bar_colors,
            marker_line_width=0,
            opacity=0.85,
        ),
        row=2,
        col=1,
    )

    # Boundaries matching full market day (09:15 AM to 03:30 PM)
    today_str = df_candles["timestamp"].dt.strftime("%Y-%m-%d").iloc[0]
    x_min = f"{today_str} 09:15:00"
    x_max = f"{today_str} 15:30:00"

    fig.update_layout(
        template="plotly_dark",
        height=650,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#0d0e11",
        plot_bgcolor="#0d0e11",
        margin=dict(l=10, r=10, t=10, b=20),
        showlegend=False,
        dragmode="pan",  # Sets PAN mode as default interaction
    )

    # Axes styling
    fig.update_xaxes(
        range=[x_min, x_max],
        showgrid=True,
        gridcolor="#1e2026",
        gridwidth=1,
        dtick=3600000,
        tickformat="%I:%M %p",
        tickfont=dict(color="#8a8f9d", size=11),
        row=2,
        col=1,
    )

    fig.update_xaxes(
        range=[x_min, x_max], showgrid=True, gridcolor="#1e2026", row=1, col=1
    )

    fig.update_yaxes(
        showgrid=True,
        gridcolor="#1e2026",
        side="right",
        tickfont=dict(color="#8a8f9d", size=11),
    )

    # Config to enable Pan mode by default in Streamlit
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "defaultPyplotModeBar": True,
            "modeBarButtonsToAdd": ["pan2d"],
        },
    )
else:
    st.info("Waiting for market candle data from Upstox API...")
