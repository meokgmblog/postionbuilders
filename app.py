import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

# ================================================================
# CONFIGURATION & CONSTANTS
# ================================================================
st.set_page_config(page_title="NIFTY 50 Position Builder", layout="wide")
st.title("📈 NIFTY 50 - Live 3 Minute Position Builder")

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiJIWjYwMzgiLCJqdGkiOiI2YTlhNTdlYmRmZmFlZTE4YjlhZWEwODEiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6dHJ1ZSwiaXNFeHRlbmRlZCI6dHJ1ZSwiaWF0IjoxNzg4NDk5OTQ3LCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE4MjAwOTUyMDB9.u8MU3qcj4cMAr4xdjM5ogr7Z_pxdkc2h3VU3aQc2jHM"

NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
INTERVAL = 3
MARKET_START = "09:15"
MARKET_END = "15:30"
IST = ZoneInfo("Asia/Kolkata")


# ================================================================
# API HELPERS
# ================================================================
def get_headers(token):
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token.strip()}",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }


def upstox_get(url, token, params=None):
    try:
        response = requests.get(
            url, headers=get_headers(token), params=params, timeout=10
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network Error: {str(e)}")

    if response.status_code != 200:
        raise RuntimeError(
            f"Upstox HTTP {response.status_code}: {response.text[:200]}"
        )

    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Upstox API Error: {data}")

    return data


def get_nifty_index_intraday(token):
    encoded_key = quote(NIFTY_INDEX_KEY, safe="")
    cache_buster = int(time.time())
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}?_={cache_buster}"

    res = upstox_get(url, token)
    candles = res.get("data", {}).get("candles", [])

    if not candles:
        raise RuntimeError("No intraday candles returned for NIFTY 50 Index.")

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
    )

    df["timestamp"] = (
        pd.to_datetime(df["timestamp"]).dt.tz_convert(IST).dt.tz_localize(None)
    )
    return df.sort_values("timestamp").reset_index(drop=True)


def get_option_chain_oi(token, last_price):
    """Fetches real-time Open Interest for ATM ±300 strikes using Upstox Option Chain API."""
    url = "https://api.upstox.com/v2/option/chain"
    params = {"instrument_key": NIFTY_INDEX_KEY, "expiry_date": ""}

    try:
        res = upstox_get(url, token, params=params)
        chain_data = res.get("data", [])

        if not chain_data:
            return 0, 0

        atm_strike = round(last_price / 50) * 50
        min_stk, max_stk = atm_strike - 300, atm_strike + 300

        total_ce_oi = 0
        total_pe_oi = 0

        for item in chain_data:
            strike = item.get("strike_price", 0)
            if min_stk <= strike <= max_stk:
                ce_market = item.get("call_options", {}).get(
                    "market_data", {}
                )
                pe_market = item.get("put_options", {}).get(
                    "market_data", {}
                )

                total_ce_oi += ce_market.get("oi", 0)
                total_pe_oi += pe_market.get("oi", 0)

        return total_ce_oi, total_pe_oi
    except Exception:
        return 0, 0


def filter_market_hours(df):
    if df.empty:
        return df
    df = df.copy()
    df["time"] = df["timestamp"].dt.time
    start = datetime.strptime(MARKET_START, "%H:%M").time()
    end = datetime.strptime(MARKET_END, "%H:%M").time()
    df = df[(df["time"] >= start) & (df["time"] <= end)].copy()
    return df.drop(columns=["time"]).reset_index(drop=True)


# ================================================================
# SESSION STATE OI TRACKER & CALCULATOR
# ================================================================
if "oi_history" not in st.session_state:
    st.session_state.oi_history = {}


def update_and_calculate_position_builder(df, current_ce_oi, current_pe_oi):
    latest_ts = df["timestamp"].iloc[-1]

    # Store latest live OI against current timestamp
    if current_ce_oi > 0 or current_pe_oi > 0:
        st.session_state.oi_history[latest_ts] = (current_ce_oi, current_pe_oi)

    # Build OI series aligned to index dataframe timestamps
    ce_list, pe_list = [], []
    last_ce, last_pe = current_ce_oi, current_pe_oi

    for ts in df["timestamp"]:
        if ts in st.session_state.oi_history:
            last_ce, last_pe = st.session_state.oi_history[ts]
        ce_list.append(last_ce)
        pe_list.append(last_pe)

    df["ce_oi"] = ce_list
    df["pe_oi"] = pe_list

    # Calculate OI differences bar-by-bar
    df["ce_oi_diff"] = df["ce_oi"].diff(1).fillna(0)
    df["pe_oi_diff"] = df["pe_oi"].diff(1).fillna(0)
    df["net_oi_change"] = df["pe_oi_diff"] - df["ce_oi_diff"]

    # Fallback simulation if session just started (derives position from volume & price)
    if (df["net_oi_change"] == 0).all():
        price_change = df["close"] - df["open"]
        df["net_oi_change"] = price_change * df["volume"]

    max_val = max(
        abs(df["net_oi_change"].min()), abs(df["net_oi_change"].max()), 1
    )
    df["position_builder_scaled"] = (df["net_oi_change"] / max_val) * 100

    return df


# ================================================================
# STREAMLIT CHART RENDERING
# ================================================================
def render_chart(df):
    last_price = df["close"].iloc[-1]
    last_time = df["timestamp"].iloc[-1].strftime("%H:%M:%S")

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.68, 0.32],
        subplot_titles=(
            f"NIFTY 50 | 3m | Last: {last_price:.2f} | Updated: {last_time} IST",
            "POSITION BUILDER HISTOGRAM",
        ),
    )

    # 1. Candlesticks Trace
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="NIFTY",
            increasing_fillcolor="#089981",
            increasing_line_color="#089981",
            decreasing_fillcolor="#f23645",
            decreasing_line_color="#f23645",
            whiskerwidth=0.4,
            hoverinfo="x+name",
        ),
        row=1,
        col=1,
    )

    # 2. Position Builder Histogram
    values = df["position_builder_scaled"].fillna(0)
    colors = ["#089981" if v >= 0 else "#f23645" for v in values]

    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=values,
            name="Net OI Scaled",
            marker_color=colors,
            marker_line_width=0,
            hovertemplate="OI Scaled: %{y:.2f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#131722",
        plot_bgcolor="#131722",
        height=720,
        margin=dict(l=15, r=15, t=35, b=15),
        showlegend=False,
        hovermode="x unified",
        dragmode="pan",
        xaxis_rangeslider_visible=False,
    )

    fig.update_xaxes(
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#89929e",
        spikethickness=1,
        spikedash="dash",
        gridcolor="#2a2e39",
        rangebreaks=[dict(bounds=["sat", "mon"])],
    )

    fig.update_yaxes(gridcolor="#2a2e39", zerolinecolor="#363a45", row=1, col=1)
    fig.update_yaxes(
        range=[-110, 110],
        gridcolor="#2a2e39",
        zerolinecolor="#363a45",
        row=2,
        col=1,
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "modeBarButtonsToAdd": ["pan2d"],
        "displaylogo": False,
    }

    st.plotly_chart(fig, use_container_width=True, config=config)


# ================================================================
# MAIN EXECUTION & AUTOMATIC CANDLE SYNC
# ================================================================
data_source_mode = st.radio(
    "Select OI Source (TradeFinder uses Current Expiry Weekly Options):",
    ["Current Weekly Expiry Options (TradeFinder Mode)", "Monthly Futures"],
    horizontal=True,
)

chart_placeholder = st.empty()

with chart_placeholder.container():
    try:
        idx_df = filter_market_hours(get_nifty_index_intraday(ACCESS_TOKEN))
        last_price = idx_df["close"].iloc[-1]

        # Fetch real-time Open Interest directly from option chain
        ce_oi, pe_oi = get_option_chain_oi(ACCESS_TOKEN, last_price)

        builder_df = update_and_calculate_position_builder(
            idx_df, ce_oi, pe_oi
        )
        render_chart(builder_df)

    except Exception as err:
        st.error(f"Execution Error: {str(err)}")

# Calculate exact sync time for 3-minute boundaries (+8 seconds latency)
now = datetime.now(IST)
seconds_past_interval = (now.minute % 3) * 60 + now.second
wait_time = 180 - seconds_past_interval + 8

status_info = st.info(f"⏳ Next candle sync in {wait_time} seconds...")
time.sleep(wait_time)
status_info.empty()
st.rerun()
