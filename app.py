import concurrent.futures
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Live Options Apex - Multi-Strike")

# User Token
UPSTOX_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"
SPOT_KEY = "NSE_INDEX|Nifty 50"


# ==========================================
# 1. UPSTOX LIVE API INTEGRATION
# ==========================================
def fetch_upstox_intraday_ohlc(instrument_key, interval="3minute"):
    """Fetches real intraday candle data strictly up to current timestamp."""
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/{interval}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }

    try:
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            candles = res.json().get("data", {}).get("candles", [])
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
                    "vol",
                    "oi",
                ],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # Strict filter up to current system time (No future candles)
            df = df[df["timestamp"] <= pd.Timestamp.now()]
            return df.sort_values("timestamp").reset_index(drop=True)
        else:
            st.error(f"API Request Failed [{res.status_code}]: {res.text}")
    except Exception as e:
        st.error(f"Upstox Request Error: {e}")

    return pd.DataFrame()


def fetch_option_chain(spot_key, expiry_date):
    """Fetches Option Chain to identify current ATM and nearby Option Keys."""
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={spot_key}&expiry_date={expiry_date}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }

    try:
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            return res.json().get("data", [])
    except Exception:
        pass
    return []


def fetch_strike_oi_parallel(keys, interval):
    """Fetches OI changes concurrently across multiple strike keys."""

    def worker(key):
        df = fetch_upstox_intraday_ohlc(key, interval)
        if not df.empty:
            df["oi_diff"] = df["oi"].diff().fillna(0)
            return df[["timestamp", "oi_diff"]]
        return pd.DataFrame()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, k) for k in keys]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if not res.empty:
                results.append(res)

    if results:
        return (
            pd.concat(results).groupby("timestamp", as_index=False)["oi_diff"].sum()
        )
    return pd.DataFrame()


def build_options_apex_dataset(interval, num_strikes, expiry_date):
    # Fetch Nifty Spot Candles
    df_spot = fetch_upstox_intraday_ohlc(SPOT_KEY, interval)

    if df_spot.empty:
        st.warning(
            "Could not fetch Nifty 50 Spot data. Verify token status or market hours."
        )
        return pd.DataFrame()

    # Get Option chain to isolate strikes around ATM
    chain = fetch_option_chain(SPOT_KEY, expiry_date)
    if not chain:
        # Fallback: Render Nifty spot candles with Price Momentum if chain expiry date is invalid
        df_spot["pos_builder"] = (df_spot["close"] - df_spot["open"]) * 100
        df_spot["color"] = df_spot["pos_builder"].apply(
            lambda x: "#10b981" if x >= 0 else "#ef4444"
        )
        return df_spot

    chain = sorted(chain, key=lambda x: x.get("strike_price", 0))
    spot_price = df_spot["close"].iloc[-1]

    closest_idx = min(
        range(len(chain)),
        key=lambda i: abs(chain[i]["strike_price"] - spot_price),
    )

    start_i = max(0, closest_idx - num_strikes)
    end_i = min(len(chain), closest_idx + num_strikes + 1)
    selected = chain[start_i:end_i]

    call_keys = [
        item["call_options"]["instrument_key"]
        for item in selected
        if "call_options" in item
    ]
    put_keys = [
        item["put_options"]["instrument_key"]
        for item in selected
        if "put_options" in item
    ]

    df_calls = fetch_strike_oi_parallel(call_keys, interval)
    df_puts = fetch_strike_oi_parallel(put_keys, interval)

    if df_calls.empty or df_puts.empty:
        df_spot["pos_builder"] = (df_spot["close"] - df_spot["open"]) * 100
        df_spot["color"] = df_spot["pos_builder"].apply(
            lambda x: "#10b981" if x >= 0 else "#ef4444"
        )
        return df_spot

    # Consolidate Net Multi-Strike Position Builder: Put Delta OI - Call Delta OI
    merged = pd.merge(df_spot, df_calls.rename(columns={"oi_diff": "call_oi_diff"}), on="timestamp", how="inner")
    merged = pd.merge(merged, df_puts.rename(columns={"oi_diff": "put_oi_diff"}), on="timestamp", how="inner")

    merged["pos_builder"] = merged["put_oi_diff"] - merged["call_oi_diff"]
    merged["color"] = merged["pos_builder"].apply(
        lambda x: "#10b981" if x >= 0 else "#ef4444"
    )

    return merged


# ==========================================
# 2. STREAMLIT INTERFACE
# ==========================================
col_title, col_tf, col_strikes, col_exp = st.columns([4, 2, 2, 2])

with col_title:
    st.markdown("### **Nifty 50 - Multi-Strike Apex**")

with col_tf:
    tf_option = st.selectbox(
        "Timeframe",
        options=["3minute", "5minute", "1minute"],
        index=0,
        label_visibility="collapsed",
    )

with col_strikes:
    strike_count = st.selectbox(
        "Strikes Range",
        options=[3, 5, 10],
        index=1,
        label_visibility="collapsed",
    )

with col_exp:
    expiry_input = st.date_input(
        "Expiry",
        value=datetime.now(),
        label_visibility="collapsed",
    )

# Fetch Data
df = build_options_apex_dataset(
    tf_option, strike_count, expiry_input.strftime("%Y-%m-%d")
)

# ==========================================
# 3. PLOTLY CHART ENGINE
# ==========================================
if not df.empty:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.75, 0.25],
    )

    # Spot Candlestick
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

    # Multi-Strike Net OI Change (Position Builder)
    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["pos_builder"],
            marker_color=df["color"],
            name="Position Builder (Put ΔOI - Call ΔOI)",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0c0e12",
        plot_bgcolor="#0c0e12",
        margin=dict(l=10, r=40, t=10, b=10),
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
        range=[
            df["timestamp"].min() - pd.Timedelta(minutes=5),
            df["timestamp"].max() + pd.Timedelta(minutes=5),
        ],
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
        zeroline=True,
        zerolinecolor="#374151",
        row=2,
        col=1,
    )

    st.plotly_chart(fig, use_container_width=True)
