import gzip
import io
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import streamlit as st

# Auto-refresh helper (Re-runs app every 180 seconds / 3 minutes)
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=180000, key="position_builder_autorefresh")
except ImportError:
    pass

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
# API HELPERS (UNCALCHED FOR LIVE DATA)
# ================================================================
def get_headers(token):
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token.strip()}",
        "Cache-Control": "no-cache"
    }

def upstox_get(url, token, params=None):
    try:
        response = requests.get(
            url, headers=get_headers(token), params=params, timeout=20
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network Error: {str(e)}")

    if response.status_code != 200:
        raise RuntimeError(f"Upstox HTTP {response.status_code}: {response.text[:200]}")

    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Upstox API Error: {data}")

    return data

# NO CACHE - Forces fresh API fetch every re-run
def get_nifty_index_intraday(token):
    encoded_key = quote(NIFTY_INDEX_KEY, safe="")
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

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

# CACHED - Instruments only need to be downloaded once per session
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

        key_col = "instrument_key" if "instrument_key" in df.columns else "instrument_token"
        sym_col = "trading_symbol" if "trading_symbol" in df.columns else "tradingsymbol"
        type_col = "instrument_type" if "instrument_type" in df.columns else "segment"
        name_col = "name" if "name" in df.columns else ("asset_symbol" if "asset_symbol" in df.columns else sym_col)

        mask = (
            df[name_col].astype(str).str.upper().isin(["NIFTY", "NIFTY 50"])
            | df[sym_col].astype(str).str.upper().str.startswith("NIFTY")
        )
        nifty_df = df[mask].copy()

        nifty_df["expiry_dt"] = pd.to_datetime(nifty_df["expiry"], errors="coerce")
        nifty_df = nifty_df.dropna(subset=["expiry_dt"])
        today = pd.Timestamp(datetime.now().date())

        active_df = nifty_df[nifty_df["expiry_dt"].dt.date >= today.date()].sort_values("expiry_dt")

        futs = active_df[active_df[type_col].astype(str).str.upper().str.contains("FUT")]
        fut_key = futs.iloc[0][key_col] if not futs.empty else None
        fut_sym = futs.iloc[0][sym_col] if not futs.empty else "NIFTY FUT"

        opts = active_df[
            active_df[type_col].astype(str).str.upper().str.contains("OPTIDX|OPTSTK|CE|PE", regex=True)
        ]

        if opts.empty:
            return fut_key, fut_sym, pd.DataFrame(), key_col, sym_col, type_col

        nearest_expiry = opts.iloc[0]["expiry_dt"]
        matching_opts = opts[opts["expiry_dt"] == nearest_expiry].copy()

        return fut_key, fut_sym, matching_opts, key_col, sym_col, type_col

    except Exception as e:
        raise RuntimeError(f"Master file parsing error: {str(e)}")

# NO CACHE - Fetches live OI per derivative strike
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
            columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
        )

        df["timestamp"] = (
            pd.to_datetime(df["timestamp"]).dt.tz_convert(IST).dt.tz_localize(None)
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
    clean_price = price_df[["timestamp", "open", "high", "low", "close"]].copy()

    opts_merged = pd.merge(ce_df, pe_df, on="timestamp", how="inner").sort_values("timestamp")
    df = pd.merge(clean_price, opts_merged, on="timestamp", how="inner").sort_values("timestamp")

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
    fig = plt.figure(figsize=(14, 7), facecolor="#0c1117")
    gs = fig.add_gridspec(5, 1, hspace=0.04)

    ax_price = fig.add_subplot(gs[:4, 0])
    ax_position = fig.add_subplot(gs[4, 0], sharex=ax_price)

    ax_price.set_facecolor("#0c1117")
    ax_position.set_facecolor("#0c1117")

    width = (3 / (24 * 60)) * 0.75
    for _, row in df.iterrows():
        t, o, h, l, c = (
            row["timestamp"],
            row["open"],
            row["high"],
            row["low"],
            row["close"],
        )
        color = "#19b5a5" if c >= o else "#ff4d5a"
        ax_price.plot([t, t], [l, h], color=color, linewidth=0.8, zorder=2)
        bottom = min(o, c)
        height = max(abs(c - o), df["close"].mean() * 0.00002)
        ax_price.bar(
            t,
            height,
            bottom=bottom,
            width=width,
            color=color,
            edgecolor=color,
            linewidth=0,
            zorder=3,
        )

    p_width = (3 / (24 * 60)) * 0.78
    values = df["position_builder_scaled"].fillna(0)
    colors = np.where(values >= 0, "#19b5a5", "#ff4d5a")
    ax_position.bar(
        df["timestamp"],
        values,
        width=p_width,
        color=colors,
        edgecolor=colors,
        linewidth=0,
    )
    ax_position.axhline(0, linewidth=0.8, color="#30343b")
    ax_position.set_ylim(-110, 110)

    last_price = df["close"].iloc[-1]
    last_time = df["timestamp"].iloc[-1].strftime("%H:%M:%S")

    ax_price.set_title(
        f"NIFTY 50   |   3m   |   Last: {last_price:.2f}   |   Updated: {last_time} IST",
        loc="left",
        fontsize=13,
        fontweight="bold",
        color="white",
        pad=10,
    )
    ax_position.text(
        0.005,
        0.88,
        "POSITION BUILDER HISTOGRAM",
        transform=ax_position.transAxes,
        fontsize=8,
        fontweight="bold",
        color="#b8c0cc",
        va="top",
    )
    ax_price.text(
        0.995,
        0.95,
        f"OI Source: {source_label}",
        transform=ax_price.transAxes,
        ha="right",
        va="top",
        fontsize=8,
        color="#9aa4b2",
    )

    for ax in [ax_price, ax_position]:
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(colors="#89929e", labelsize=8)

    ax_price.tick_params(labelbottom=False)
    ax_position.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
    ax_position.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    st.pyplot(fig)

# ================================================================
# MAIN ENTRYPOINT
# ================================================================
try:
    data_source_mode = st.radio(
        "Select OI Source (TradeFinder uses Current Expiry Weekly Options):",
        ["Current Weekly Expiry Options (TradeFinder Mode)", "Monthly Futures"],
        horizontal=True,
    )

    with st.spinner("Fetching Live NIFTY Index and Weekly Options Data..."):
        idx_df = filter_market_hours(get_nifty_index_intraday(ACCESS_TOKEN))
        fut_key, fut_sym, opts_df, key_col, sym_col, type_col = (
            fetch_upstox_nifty_instruments()
        )

        if "Weekly" in data_source_mode and not opts_df.empty:
            last_close = idx_df["close"].iloc[-1]
            strike_col = "strike_price" if "strike_price" in opts_df.columns else "strike"
            opts_df["strike_num"] = pd.to_numeric(opts_df[strike_col], errors="coerce")

            atm_strike = round(last_close / 50) * 50
            min_stk, max_stk = atm_strike - 300, atm_strike + 300
            atm_opts = opts_df[(opts_df["strike_num"] >= min_stk) & (opts_df["strike_num"] <= max_stk)].copy()

            if atm_opts.empty:
                atm_opts = opts_df

            ce_opts = atm_opts[atm_opts[sym_col].astype(str).str.endswith("CE")]
            pe_opts = atm_opts[atm_opts[sym_col].astype(str).str.endswith("PE")]

            ce_df = None
            for _, row in ce_opts.iterrows():
                opt_data = filter_market_hours(get_derivative_intraday(ACCESS_TOKEN, row[key_col]))
                if not opt_data.empty:
                    opt_sub = opt_data[["timestamp", "oi"]].copy()
                    if ce_df is None:
                        ce_df = opt_sub.rename(columns={"oi": "ce_oi"})
                    else:
                        ce_df = pd.merge(ce_df, opt_sub, on="timestamp", how="outer")
                        ce_df["ce_oi"] = ce_df["ce_oi"].fillna(0) + ce_df["oi"].fillna(0)
                        ce_df.drop(columns={"oi"], inplace=True)

            pe_df = None
            for _, row in pe_opts.iterrows():
                opt_data = filter_market_hours(get_derivative_intraday(ACCESS_TOKEN, row[key_col]))
                if not opt_data.empty:
                    opt_sub = opt_data[["timestamp", "oi"]].copy()
                    if pe_df is None:
                        pe_df = opt_sub.rename(columns={"oi": "pe_oi"})
                    else:
                        pe_df = pd.merge(pe_df, opt_sub, on="timestamp", how="outer")
                        pe_df["pe_oi"] = pe_df["pe_oi"].fillna(0) + pe_df["oi"].fillna(0)
                        pe_df.drop(columns={"oi"], inplace=True)

            if ce_df is not None and pe_df is not None:
                ce_df = ce_df.sort_values("timestamp").ffill().dropna()
                pe_df = pe_df.sort_values("timestamp").ffill().dropna()

                builder_df = calculate_tradefinder_position_builder(idx_df, ce_df, pe_df)
                exp_date_str = opts_df.iloc[0]["expiry_dt"].strftime("%b-%d")
                source_tag = f"NIFTY Weekly Options ({exp_date_str})"
            else:
                st.error("Failed to fetch option contracts.")
                st.stop()
        else:
            st.error("Select TradeFinder Mode to compare options Open Interest.")
            st.stop()

    st.success(f"Connected to {source_tag} | Timezone: IST (UTC+5:30)")
    render_chart(builder_df, source_tag)

except Exception as err:
    st.error(f"Execution Error: {str(err)}")
