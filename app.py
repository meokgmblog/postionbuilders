import gzip
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo
import numpy as np
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

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YTMwY2UxNTY4ODI0Zjc3ZDc1NmU3NjgiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlzRXh0ZW5kZWQiOnRydWUsImlhdCI6MTc4MTU4MzM4MSwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxODEzMTgzMjAwfQ.IoRDQhbhcn3w9Fkw75N3eBSamLcaA8GcAhVjf5K-iL8"

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


@st.cache_data(ttl=3600)
def fetch_upstox_nifty_instruments():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz"

    try:
        res = requests.get(url, timeout=15)
        if res.status_code != 200:
            raise Exception(f"HTTP {res.status_code} while downloading file.")

        with gzip.open(io.BytesIO(res.content), "rt") as f:
            df = pd.read_csv(f)

        df.columns = [c.lower() for c in df.columns]

        key_col = (
            "instrument_key"
            if "instrument_key" in df.columns
            else "instrument_token"
        )
        sym_col = (
            "trading_symbol"
            if "trading_symbol" in df.columns
            else "tradingsymbol"
        )
        type_col = (
            "instrument_type"
            if "instrument_type" in df.columns
            else "segment"
        )
        name_col = (
            "name"
            if "name" in df.columns
            else ("asset_symbol" if "asset_symbol" in df.columns else sym_col)
        )

        mask = (
            df[name_col].astype(str).str.upper().isin(["NIFTY", "NIFTY 50"])
            | df[sym_col].astype(str).str.upper().str.startswith("NIFTY")
        )
        nifty_df = df[mask].copy()

        nifty_df["expiry_dt"] = pd.to_datetime(
            nifty_df["expiry"], errors="coerce"
        )
        nifty_df = nifty_df.dropna(subset=["expiry_dt"])
        today = pd.Timestamp(datetime.now().date())

        active_df = nifty_df[
            nifty_df["expiry_dt"].dt.date >= today.date()
        ].sort_values("expiry_dt")

        futs = active_df[
            active_df[type_col].astype(str).str.upper().str.contains("FUT")
        ]
        fut_key = futs.iloc[0][key_col] if not futs.empty else None
        fut_sym = futs.iloc[0][sym_col] if not futs.empty else "NIFTY FUT"

        opts = active_df[
            active_df[type_col]
            .astype(str)
            .str.upper()
            .str.contains("OPTIDX|OPTSTK|CE|PE", regex=True)
        ]

        if opts.empty:
            return fut_key, fut_sym, pd.DataFrame(), key_col, sym_col, type_col

        nearest_expiry = opts.iloc[0]["expiry_dt"]
        matching_opts = opts[opts["expiry_dt"] == nearest_expiry].copy()

        return fut_key, fut_sym, matching_opts, key_col, sym_col, type_col

    except Exception as e:
        raise RuntimeError(f"Master file parsing error: {str(e)}")


def get_derivative_intraday(token, instrument_key):
    if not instrument_key:
        return pd.DataFrame()

    encoded_key = quote(str(instrument_key), safe="")
    cache_buster = int(time.time())
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}?_={cache_buster}"

    try:
        res = upstox_get(url, token)
        candles = res.get("data", {}).get("candles", [])

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

        df["timestamp"] = (
            pd.to_datetime(df["timestamp"])
            .dt.tz_convert(IST)
            .dt.tz_localize(None)
        )
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def fetch_option_data_parallel(token, option_rows, key_col):
    keys = [row[key_col] for _, row in option_rows.iterrows()]

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(
            executor.map(
                lambda key: filter_market_hours(
                    get_derivative_intraday(token, key)
                ),
                keys,
            )
        )

    combined_df = None
    for opt_data in results:
        if not opt_data.empty:
            opt_sub = opt_data[["timestamp", "oi"]].copy()
            if combined_df is None:
                combined_df = opt_sub.rename(columns={"oi": "sum_oi"})
            else:
                combined_df = pd.merge(
                    combined_df, opt_sub, on="timestamp", how="outer"
                )
                combined_df["sum_oi"] = combined_df["sum_oi"].fillna(
                    0
                ) + combined_df["oi"].fillna(0)
                combined_df.drop(columns=["oi"], inplace=True)

    return combined_df


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
# POSITION BUILDER CALCULATION
# ================================================================
def calculate_tradefinder_position_builder(price_df, ce_df, pe_df):
    clean_price = price_df[
        ["timestamp", "open", "high", "low", "close"]
    ].copy()

    opts_merged = pd.merge(ce_df, pe_df, on="timestamp", how="inner").sort_values(
        "timestamp"
    )
    df = pd.merge(clean_price, opts_merged, on="timestamp", how="inner").sort_values(
        "timestamp"
    )

    if df.empty:
        raise RuntimeError("Timestamp alignment mismatch across market feeds.")

    df["ce_oi_diff"] = df["ce_oi"].diff(1).fillna(0)
    df["pe_oi_diff"] = df["pe_oi"].diff(1).fillna(0)

    df["net_oi_change"] = df["pe_oi_diff"] - df["ce_oi_diff"]

    max_val = max(abs(df["net_oi_change"].min()), abs(df["net_oi_change"].max()), 1)
    df["position_builder_scaled"] = (df["net_oi_change"] / max_val) * 100

    return df


# ================================================================
# STREAMLIT CHART RENDERING
# ================================================================
def render_chart(df, source_label):
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

# Container for holding the live chart element
chart_placeholder = st.empty()

with chart_placeholder.container():
    try:
        idx_df = filter_market_hours(get_nifty_index_intraday(ACCESS_TOKEN))
        fut_key, fut_sym, opts_df, key_col, sym_col, type_col = (
            fetch_upstox_nifty_instruments()
        )

        if "Weekly" in data_source_mode and not opts_df.empty:
            last_close = idx_df["close"].iloc[-1]
            strike_col = (
                "strike_price" if "strike_price" in opts_df.columns else "strike"
            )
            opts_df["strike_num"] = pd.to_numeric(
                opts_df[strike_col], errors="coerce"
            )

            atm_strike = round(last_close / 50) * 50
            min_stk, max_stk = atm_strike - 300, atm_strike + 300
            atm_opts = opts_df[
                (opts_df["strike_num"] >= min_stk)
                & (opts_df["strike_num"] <= max_stk)
            ].copy()

            if atm_opts.empty:
                atm_opts = opts_df

            ce_opts = atm_opts[atm_opts[sym_col].astype(str).str.endswith("CE")]
            pe_opts = atm_opts[atm_opts[sym_col].astype(str).str.endswith("PE")]

            ce_df = fetch_option_data_parallel(ACCESS_TOKEN, ce_opts, key_col)
            pe_df = fetch_option_data_parallel(ACCESS_TOKEN, pe_opts, key_col)

            if ce_df is not None and pe_df is not None:
                ce_df = (
                    ce_df.rename(columns={"sum_oi": "ce_oi"})
                    .sort_values("timestamp")
                    .ffill()
                    .dropna()
                )
                pe_df = (
                    pe_df.rename(columns={"sum_oi": "pe_oi"})
                    .sort_values("timestamp")
                    .ffill()
                    .dropna()
                )

                builder_df = calculate_tradefinder_position_builder(
                    idx_df, ce_df, pe_df
                )
                exp_date_str = opts_df.iloc[0]["expiry_dt"].strftime("%b-%d")
                source_tag = f"NIFTY Weekly Options ({exp_date_str})"
                render_chart(builder_df, source_tag)
            else:
                st.error("Failed to fetch option contracts.")
        else:
            st.error("Select TradeFinder Mode to compare options Open Interest.")

    except Exception as err:
        st.error(f"Execution Error: {str(err)}")

# Calculate seconds remaining to next 3-minute candle boundary (+8 seconds latency offset)
now = datetime.now(IST)
seconds_past_interval = (now.minute % 3) * 60 + now.second
wait_time = 180 - seconds_past_interval + 8

# Displays status and waits until the exact moment of candle closing
status_info = st.info(f"⏳ Next candle sync in {wait_time} seconds...")
time.sleep(wait_time)
status_info.empty()
st.rerun()
