import gzip
import io
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

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI6M0FZSEUiLCJqdGkiOiI6YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"

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
        "Cache-Control": "no-cache",
    }


def upstox_get(url, token, params=None):
    try:
        response = requests.get(
            url, headers=get_headers(token), params=params, timeout=20
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
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

    res = upstox_get(url, token)
    candles = res.get("data", {}).get("candles", [])

    if not candles:
        raise RuntimeError(
            "No intraday candles returned for NIFTY 50 Index."
        )

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
        res = requests.get(url, timeout=30)
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
        fut_sym = (
            futs.iloc[0][sym_col] if not futs.empty else "NIFTY FUT"
        )

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
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

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
        vertical_spacing=0.04,
        row_heights=[0.7, 0.3],
        subplot_titles=(
            f"NIFTY 50 | 3m | Last: {last_price:.2f} | Updated: {last_time} IST",
            "POSITION BUILDER HISTOGRAM",
        ),
    )

    # 1. Candlestick Chart
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="NIFTY 50",
            increasing_line_color="#19b5a5",
            decreasing_line_color="#ff4d5a",
            hoverinfo="x+name",
        ),
        row=1,
        col=1,
    )

    # 2. Position Builder Bar Chart
    values = df["position_builder_scaled"].fillna(0)
    colors = ["#19b5a5" if v >= 0 else "#ff4d5a" for v in values]

    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=values,
            name="Net OI Scaled",
            marker_color=colors,
            marker_line_width=0,
            hovertemplate="Time: %{x}<br>OI Scaled: %{y:.2f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0c1117",
        plot_bgcolor="#0c1117",
        height=700,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=False,
        hovermode="x",
        dragmode="pan",
        xaxis_rangeslider_visible=False,
    )

    fig.update_yaxes(gridcolor="#1e2631", zerolinecolor="#30343b", row=1, col=1)
    fig.update_yaxes(
        range=[-110, 110],
        gridcolor="#1e2631",
        zerolinecolor="#30343b",
        row=2,
        col=1,
    )
    fig.update_xaxes(
        gridcolor="#1e2631", rangebreaks=[dict(bounds=["sat", "mon"])]
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "modeBarButtonsToAdd": ["pan2d"],
        "displaylogo": False,
    }

    st.plotly_chart(fig, use_container_width=True, config=config)


# ================================================================
# LIVE DATA FEED & AUTO-REFRESH FRAGMENT
# ================================================================
@st.fragment(run_every=180)  # Automatically re-executes every 180 seconds (3 mins)
def live_dashboard(data_source_mode):
    try:
        with st.spinner("Fetching Live NIFTY Candles & OI Data..."):
            idx_df = filter_market_hours(get_nifty_index_intraday(ACCESS_TOKEN))
            fut_key, fut_sym, opts_df, key_col, sym_col, type_col = (
                fetch_upstox_nifty_instruments()
            )

            if "Weekly" in data_source_mode and not opts_df.empty:
                last_close = idx_df["close"].iloc[-1]
                strike_col = (
                    "strike_price"
                    if "strike_price" in opts_df.columns
                    else "strike"
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

                ce_opts = atm_opts[
                    atm_opts[sym_col].astype(str).str.endswith("CE")
                ]
                pe_opts = atm_opts[
                    atm_opts[sym_col].astype(str).str.endswith("PE")
                ]

                ce_df = None
                for _, row in ce_opts.iterrows():
                    opt_data = filter_market_hours(
                        get_derivative_intraday(ACCESS_TOKEN, row[key_col])
                    )
                    if not opt_data.empty:
                        opt_sub = opt_data[["timestamp", "oi"]].copy()
                        if ce_df is None:
                            ce_df = opt_sub.rename(columns={"oi": "ce_oi"})
                        else:
                            ce_df = pd.merge(
                                ce_df, opt_sub, on="timestamp", how="outer"
                            )
                            ce_df["ce_oi"] = (
                                ce_df["ce_oi"].fillna(0) + ce_df["oi"].fillna(0)
                            )
                            ce_df.drop(columns=["oi"], inplace=True)

                pe_df = None
                for _, row in pe_opts.iterrows():
                    opt_data = filter_market_hours(
                        get_derivative_intraday(ACCESS_TOKEN, row[key_col])
                    )
                    if not opt_data.empty:
                        opt_sub = opt_data[["timestamp", "oi"]].copy()
                        if pe_df is None:
                            pe_df = opt_sub.rename(columns={"oi": "pe_oi"})
                        else:
                            pe_df = pd.merge(
                                pe_df, opt_sub, on="timestamp", how="outer"
                            )
                            pe_df["pe_oi"] = (
                                pe_df["pe_oi"].fillna(0) + pe_df["oi"].fillna(0)
                            )
                            pe_df.drop(columns=["oi"], inplace=True)

                if ce_df is not None and pe_df is not None:
                    ce_df = ce_df.sort_values("timestamp").ffill().dropna()
                    pe_df = pe_df.sort_values("timestamp").ffill().dropna()

                    builder_df = calculate_tradefinder_position_builder(
                        idx_df, ce_df, pe_df
                    )
                    exp_date_str = opts_df.iloc[0]["expiry_dt"].strftime(
                        "%b-%d"
                    )
                    source_tag = f"NIFTY Weekly Options ({exp_date_str})"
                else:
                    st.error("Failed to fetch option contracts.")
                    return
            else:
                st.error(
                    "Select TradeFinder Mode to compare options Open Interest."
                )
                return

        st.success(f"Connected to {source_tag} | Timezone: IST (UTC+5:30)")
        render_chart(builder_df, source_tag)

    except Exception as err:
        st.error(f"Execution Error: {str(err)}")


# ================================================================
# MAIN ENTRYPOINT
# ================================================================
data_source_mode = st.radio(
    "Select OI Source (TradeFinder uses Current Expiry Weekly Options):",
    ["Current Weekly Expiry Options (TradeFinder Mode)", "Monthly Futures"],
    horizontal=True,
)

# Run live updates inside dedicated fragment
live_dashboard(data_source_mode)
