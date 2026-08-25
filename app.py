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
# 1. UPSTOX V3 DIRECT INTRADAY FETCH
# ==========================================
def fetch_upstox_v3_intraday(instrument_key, interval_min=3):
    """Fetches intraday OHLCV candles using Upstox V3 API directly in target timeframe."""
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{instrument_key}/minutes/{interval_min}"
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
            # Remove timezone offset (+05:30) for clean plotting
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(
                None
            )

            # Sort ascending: 09:15 AM -> 03:30 PM
            df = df.sort_values("timestamp").reset_index(drop=True)

            # Strict cutoff to current execution time
            return df[df["timestamp"] <= datetime.now()]
    except Exception as e:
        st.error(f"Upstox V3 Fetch Error: {e}")

    return pd.DataFrame()


def get_expiry_dates(spot_key):
    """Retrieves all active expiry dates from Upstox Option Chain."""
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={spot_key}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }
    try:
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json().get("data", [])
            expiries = sorted(
                list({item["expiry"] for item in data if "expiry" in item})
            )
            if expiries:
                return expiries
    except Exception:
        pass
    return [datetime.now().strftime("%Y-%m-%d")]


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


def fetch_strike_oi_parallel(keys, interval_min):
    """Fetches option strike candles in parallel using V3 Endpoint."""

    def worker(key):
        df = fetch_upstox_v3_intraday(key, interval_min)
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
            pd.concat(results)
            .groupby("timestamp", as_index=False)["oi_diff"]
            .sum()
        )
    return pd.DataFrame()


def build_options_apex_dataset(timeframe_str, num_strikes, selected_expiry):
    interval_min = 3 if "3" in timeframe_str else 5

    # 1. Fetch Nifty Spot Candles
    df_spot = fetch_upstox_v3_intraday(SPOT_KEY, interval_min)
    if df_spot.empty:
        return pd.DataFrame()

    # Default fallback histogram values based on spot momentum
    df_spot["pos_builder"] = (df_spot["close"] - df_spot["open"]) * 10
    df_spot["color"] = df_spot["pos_builder"].apply(
        lambda x: "#10b981" if x >= 0 else "#ef4444"
    )

    # 2. Fetch Option Chain
    chain = fetch_option_chain(SPOT_KEY, selected_expiry)
    if not chain:
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

    # 3. Parallel Fetch Strike OI
    df_calls = fetch_strike_oi_parallel(call_keys, interval_min)
    df_puts = fetch_strike_oi_parallel(put_keys, interval_min)

    if df_calls.empty or df_puts.empty:
        return df_spot

    # 4. Safe Left Join (Preserves ALL Spot Candles)
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

    # Position Builder = Put ΔOI - Call ΔOI
    real_pos_builder = merged["put_oi_diff"] - merged["call_oi_diff"]

    # Only override default momentum if option OI returns non-zero values
    if not (real_pos_builder == 0).all():
        merged["pos_builder"] = real_pos_builder
        merged["color"] = merged["pos_builder"].apply(
            lambda x: "#10b981" if x >= 0 else "#ef4444"
        )

    return merged


# ==========================================
# 2. UI CONTROLS
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
    available_expiries = get_expiry_dates(SPOT_KEY)
    expiry_input = st.selectbox(
        "Expiry Date",
        options=available_expiries,
        index=0,
        label_visibility="collapsed",
    )

df = build_options_apex_dataset(tf_option, strike_count, expiry_input)

# ==========================================
# 3. PLOTLY GRAPH CANVAS
# ==========================================
if not df.empty:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.72, 0.28],
    )

    # Nifty 50 Spot Candlesticks
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Nifty 50 Spot",
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

    fig.update_xaxes(
        showgrid=True,
        gridcolor="#1f2937",
        color="#9ca3af",
        tickformat="%H:%M",
        type="date",
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
    st.error("No intraday market candles fetched. Please verify token status.")
