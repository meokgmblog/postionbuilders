import concurrent.futures
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(layout="wide", page_title="Live Nifty 50 Multi-Strike Apex")

UPSTOX_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"
SPOT_KEY = "NSE_INDEX|Nifty 50"


# ==========================================
# 1. UPSTOX FULL INTRADAY FETCH ENGINE
# ==========================================
def fetch_full_intraday_candles(instrument_key):
    """Fetches all 1-minute intraday candles for today from Upstox API."""
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/1minute"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }

    try:
        res = requests.get(url, headers=headers, timeout=8)
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
            # Remove timezone offset to make naive IST
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(
                None
            )

            # Sort ascending (09:15 AM -> Current Time)
            df = df.sort_values("timestamp").reset_index(drop=True)

            # Cap strictly at current system time
            df = df[df["timestamp"] <= datetime.now()]
            return df
    except Exception:
        pass

    return pd.DataFrame()


def resample_ohlc(df, timeframe="3min"):
    """Resamples 1m candles accurately into target buckets (3min / 5min)."""
    if df.empty:
        return pd.DataFrame()

    # Map timeframe labels
    tf = "3min" if timeframe in ["3min", "3minute"] else "5min"

    df_res = (
        df.set_index("timestamp")
        .resample(tf, origin="start_day")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "vol": "sum",
                "oi": "last",
            }
        )
        .dropna()
        .reset_index()
    )
    return df_res


def fetch_option_chain(spot_key, expiry_date):
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


def fetch_strike_oi_parallel(keys, timeframe):
    """Fetches full intraday option candles in parallel."""

    def worker(key):
        df_1m = fetch_full_intraday_candles(key)
        if not df_1m.empty:
            df_res = resample_ohlc(df_1m, timeframe)
            df_res["oi_diff"] = df_res["oi"].diff().fillna(0)
            return df_res[["timestamp", "oi_diff"]]
        return pd.DataFrame()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, k) for k in keys]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if not res.empty:
                results.append(res)

    if results:
        # Sum total delta OI across all fetched strikes per candle timestamp
        return (
            pd.concat(results)
            .groupby("timestamp", as_index=False)["oi_diff"]
            .sum()
        )
    return pd.DataFrame()


def build_options_apex_dataset(timeframe, num_strikes, expiry_date):
    # Fetch Base Spot Index Candles
    df_spot_1m = fetch_full_intraday_candles(SPOT_KEY)
    if df_spot_1m.empty:
        return pd.DataFrame()

    df_spot = resample_ohlc(df_spot_1m, timeframe)

    chain = fetch_option_chain(SPOT_KEY, expiry_date)
    if not chain:
        # Fallback to Price Momentum if Option Chain fails
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

    df_calls = fetch_strike_oi_parallel(call_keys, timeframe)
    df_puts = fetch_strike_oi_parallel(put_keys, timeframe)

    if df_calls.empty or df_puts.empty:
        df_spot["pos_builder"] = (df_spot["close"] - df_spot["open"]) * 100
        df_spot["color"] = df_spot["pos_builder"].apply(
            lambda x: "#10b981" if x >= 0 else "#ef4444"
        )
        return df_spot

    # OUTER MERGE WITH FFILL PREVENTS DROPPING CANDLES WHEN AN OPTION STRIKE MISSES A TIMESTAMPS
    merged = pd.merge(
        df_spot,
        df_calls.rename(columns={"oi_diff": "call_oi_diff"}),
        on="timestamp",
        how="left",
    )
    merged = pd.merge(
        merged,
        df_puts.rename(columns={"oi_diff": "put_oi_diff"}),
        on="timestamp",
        how="left",
    )

    merged["call_oi_diff"] = merged["call_oi_diff"].fillna(0)
    merged["put_oi_diff"] = merged["put_oi_diff"].fillna(0)

    # Calculate Position Builder: Net ΔOI Put - Net ΔOI Call
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
        options=["3min", "5min"],
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

df = build_options_apex_dataset(
    tf_option, strike_count, expiry_input.strftime("%Y-%m-%d")
)

# ==========================================
# 3. PLOTLY CHART CANVAS
# ==========================================
if not df.empty:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.75, 0.25],
    )

    # Spot Candlesticks
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

    # Position Builder Histogram
    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["pos_builder"],
            marker_color=df["color"],
            name="Position Builder",
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

    # Render complete range from 09:15 AM to current timestamp
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
else:
    st.error("Could not fetch live session data. Check Upstox Access Token.")
