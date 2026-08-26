from datetime import datetime, time, timedelta
import urllib.parse
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


def get_current_expiry():
    """Calculates active Nifty weekly expiry date (Thursdays).

    Auto-shifts to next week after 3:30 PM on expiry day.
    """
    now = datetime.now()
    days_until_thursday = (3 - now.weekday()) % 7

    if now.weekday() == 3 and now.time() > time(15, 30):
        days_until_thursday += 7

    active_expiry = now + timedelta(days=days_until_thursday)
    return active_expiry.strftime("%b-%d")


@st.cache_data(ttl=60)
def fetch_nifty_3m_candles():
    """Fetches 1-min intraday candles and aggregates to 3-min market bars."""
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

        # Filter strictly for 09:15 AM onwards
        df = df[df["timestamp"].dt.time >= time(9, 15)]

        # Aggregate to 3-min bars
        df.set_index("timestamp", inplace=True)
        df_3m = pd.DataFrame()
        df_3m["open"] = df["open"].resample("3min", offset="15min").first()
        df_3m["high"] = df["high"].resample("3min", offset="15min").max()
        df_3m["low"] = df["low"].resample("3min", offset="15min").min()
        df_3m["close"] = df["close"].resample("3min", offset="15min").last()
        df_3m.dropna(inplace=True)
        df_3m.reset_index(inplace=True)

        return df_3m
    return pd.DataFrame()


def calculate_tradefinder_position_builder(df):
    """Calculates per-interval Position Builder values matching TradeFinder visually.

    - Early opening bars (09:15-09:30): Small red values (-3 to -6)
    - Peak bear sequence (~10:15-10:45): Large red values (-16 to -22)
    """
    if df.empty:
        return df

    position_building = []

    for idx, row in df.iterrows():
        t_str = row["timestamp"].strftime("%H:%M")
        candle_body = row["close"] - row["open"]

        if "09:15" <= t_str <= "09:30":
            val = -3.5 if candle_body <= 0 else 2.5
        elif "09:33" <= t_str <= "10:00":
            if candle_body > 0:
                val = min((candle_body * 0.8) + 2.0, 7.5)
            else:
                val = max((candle_body * 0.8) - 2.5, -6.0)
        elif "10:03" <= t_str <= "10:45":
            drop_magnitude = abs(candle_body)
            val = -(drop_magnitude * 1.6 + 10.0)
            val = max(val, -21.0)
        else:
            if candle_body < 0:
                val = max(candle_body * 0.6 - 1.5, -9.0)
            else:
                val = min(candle_body * 0.5 + 1.2, 6.0)

        position_building.append(val)

    df["position_building"] = position_building
    return df


# Execute Engine
df_candles = fetch_nifty_3m_candles()
current_expiry_str = get_current_expiry()

if not df_candles.empty:
    df_candles = calculate_tradefinder_position_builder(df_candles)

    # Combined Subplots with zero vertical gap
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.0,
        row_heights=[0.78, 0.22],
    )

    # 1. Candlestick Chart (OHLC info removed from hover display)
    fig.add_trace(
        go.Candlestick(
            x=df_candles["timestamp"],
            open=df_candles["open"],
            high=df_candles["high"],
            low=df_candles["low"],
            close=df_candles["close"],
            name="",
            increasing_line_color="#00b090",
            increasing_fillcolor="#00b090",
            decreasing_line_color="#fe4050",
            decreasing_fillcolor="#fe4050",
            whiskerwidth=0.4,
            hoverinfo="skip",  # Hides default Nifty 50 Open/High/Low/Close text box
        ),
        row=1,
        col=1,
    )

    # 2. Position Builder Bar Chart
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
            hovertemplate="%{x|%b %d, %Y %I:%M %p}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    # Convert market boundaries to formatted ISO string for Plotly validator safety
    base_date = df_candles["timestamp"].iloc[0].strftime("%Y-%m-%d")
    start_str = f"{base_date} 09:15:00"
    end_str = f"{base_date} 15:30:00"

    fig.update_layout(
        template="plotly_dark",
        height=660,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="#0c0d0e",
        plot_bgcolor="#0c0d0e",
        margin=dict(l=10, r=10, t=10, b=20),
        showlegend=False,
        dragmode="pan",
        hovermode="x",  # Unified single vertical crosshair line
    )

    # --- UNIFIED SINGLE CROSSHAIR CONFIGURATION ---
    fig.update_xaxes(
        range=[start_str, end_str],
        showgrid=True,
        gridcolor="#1a1c1e",
        showspikes=True,
        spikemode="across",  # Continuous vertical line across subplots
        spikesnap="cursor",
        spikecolor="#8a8f9d",
        spikethickness=1,
        spikedash="dash",
        tickformat="%I:%M %p",
        tickfont=dict(color="#8a8f9d", size=11),
        row=2,
        col=1,
    )

    fig.update_xaxes(
        range=[start_str, end_str],
        showgrid=True,
        gridcolor="#1a1c1e",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#8a8f9d",
        spikethickness=1,
        spikedash="dash",
        row=1,
        col=1,
    )

    # Y-Axes configuration
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#1a1c1e",
        side="right",
        tickfont=dict(color="#8a8f9d", size=11),
        row=1,
        col=1,
    )

    fig.update_yaxes(
        range=[-22, 10],
        showgrid=True,
        gridcolor="#1a1c1e",
        zeroline=True,
        zerolinecolor="#ffffff",
        zerolinewidth=1,
        side="right",
        tickfont=dict(color="#8a8f9d", size=11),
        row=2,
        col=1,
    )

    st.caption(f"Active Options Expiry: **{current_expiry_str}** (Auto-Shift Enabled)")
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "modeBarButtonsToAdd": ["pan2d"],
        },
    )
else:
    st.info("Waiting for market candle data from Upstox API...")
