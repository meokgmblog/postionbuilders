from datetime import datetime
from urllib.parse import quote
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import streamlit as st

# ================================================================
# STREAMLIT CONFIG & SETTINGS
# ================================================================
st.set_page_config(page_title="NIFTY 50 Position Builder", layout="wide")
st.title("📈 NIFTY 50 - 3 Minute Position Builder")

ACCESS_TOKEN = st.text_input(
    "Upstox Access Token",
    value="eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI6M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU",
    type="password",
)

NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
INTERVAL = 3
MARKET_START = "09:15"
MARKET_END = "15:30"
HISTOGRAM_SCALE = 1000

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}


# ================================================================
# API HELPERS
# ================================================================
def upstox_get(url, params=None):
    try:
        response = requests.get(
            url, headers=HEADERS, params=params, timeout=20
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network Connection Error: {str(e)}")

    if response.status_code != 200:
        raise RuntimeError(
            f"Upstox HTTP {response.status_code}: {response.text[:300]}"
        )

    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Upstox API Error: {data}")

    return data


def get_nifty_index_intraday():
    encoded_key = quote(NIFTY_INDEX_KEY, safe="")
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

    res = upstox_get(url)
    candles = res.get("data", {}).get("candles", [])

    if not candles:
        raise RuntimeError("No intraday candles returned for NIFTY 50 Index.")

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def find_nearest_nifty_future():
    # Primary attempt: Instrument Search
    search_url = "https://api.upstox.com/v2/instruments/search"
    queries_to_try = ["NIFTY", "NSE_FO|NIFTY"]

    for q in queries_to_try:
        try:
            res = upstox_get(
                search_url,
                params={
                    "query": q,
                    "exchange": "NSE_FO",
                    "instrument_type": "FUT",
                },
            )
            instruments = res.get("data", [])
            valid = []
            today = pd.Timestamp(datetime.now().date())

            for item in instruments:
                # Filter for futures contract
                itype = item.get("instrument_type", "")
                tsym = item.get("trading_symbol", "")

                if "FUT" in itype or "FUT" in tsym:
                    expiry = item.get("expiry")
                    if expiry:
                        exp_dt = pd.Timestamp(expiry)
                        if exp_dt.date() >= today.date():
                            valid.append(
                                {
                                    "key": item.get("instrument_key"),
                                    "symbol": tsym,
                                    "expiry": exp_dt,
                                }
                            )

            if valid:
                valid_df = pd.DataFrame(valid).sort_values("expiry")
                return (
                    valid_df.iloc[0]["key"],
                    valid_df.iloc[0]["symbol"],
                )
        except Exception:
            continue

    # Fallback to standard monthly key convention if search API returns empty
    now = datetime.now()
    curr_month = now.strftime("%b").upper()
    curr_year = now.strftime("%y")
    fallback_key = f"NSE_FO|NIFTY{curr_year}{curr_month}FUT"
    return fallback_key, f"NIFTY {curr_month} FUT"


def get_nifty_future_intraday(future_key):
    encoded_key = quote(future_key, safe="")
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

    res = upstox_get(url)
    candles = res.get("data", {}).get("candles", [])

    if not candles:
        raise RuntimeError(
            f"No futures candle data returned for key: {future_key}"
        )

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def filter_market_hours(df):
    df = df.copy()
    df["time"] = df["timestamp"].dt.time
    start = datetime.strptime(MARKET_START, "%H:%M").time()
    end = datetime.strptime(MARKET_END, "%H:%M").time()
    df = df[(df["time"] >= start) & (df["time"] <= end)].copy()
    return df.drop(columns=["time"]).reset_index(drop=True)


# ================================================================
# POSITION BUILDER CALCULATION
# ================================================================
def calculate_position_builder(price_df, future_df):
    price = price_df.copy()
    future = future_df[["timestamp", "close", "oi"]].copy()
    future.rename(
        columns={"close": "future_close", "oi": "future_oi"}, inplace=True
    )

    df = pd.merge(price, future, on="timestamp", how="inner").sort_values(
        "timestamp"
    )

    if df.empty:
        raise RuntimeError("Timestamp mismatch between Index and Futures data.")

    df["price_change"] = df["close"].diff(1)
    df["price_change_pct"] = df["close"].pct_change(1) * 100
    df["oi_change"] = df["future_oi"].diff(1)
    df["oi_change_pct"] = df["future_oi"].pct_change(1) * 100

    def classify(row):
        p_chg = row["price_change"]
        oi_chg = row["oi_change"]

        if pd.isna(p_chg) or pd.isna(oi_chg) or oi_chg == 0:
            return "NEUTRAL"
        if p_chg > 0 and oi_chg > 0:
            return "LONG BUILDUP"
        if p_chg < 0 and oi_chg > 0:
            return "SHORT BUILDUP"
        if p_chg > 0 and oi_chg < 0:
            return "SHORT COVERING"
        if p_chg < 0 and oi_chg < 0:
            return "LONG UNWINDING"
        return "NEUTRAL"

    df["position_type"] = df.apply(classify, axis=1)
    df["position_builder"] = df["oi_change_pct"]

    bullish = ["LONG BUILDUP", "SHORT COVERING"]
    bearish = ["SHORT BUILDUP", "LONG UNWINDING"]

    df.loc[df["position_type"].isin(bullish), "position_builder"] = df[
        "position_builder"
    ].abs()
    df.loc[df["position_type"].isin(bearish), "position_builder"] = -df[
        "position_builder"
    ].abs()
    df.loc[df["position_type"] == "NEUTRAL", "position_builder"] = 0

    df["position_builder_scaled"] = df["position_builder"] * HISTOGRAM_SCALE
    return df


# ================================================================
# STREAMLIT PLOTTING & UI
# ================================================================
def render_chart(df, future_symbol):
    fig = plt.figure(figsize=(14, 7), facecolor="#0c1117")
    gs = fig.add_gridspec(5, 1, hspace=0.04)

    ax_price = fig.add_subplot(gs[:4, 0])
    ax_position = fig.add_subplot(gs[4, 0], sharex=ax_price)

    ax_price.set_facecolor("#0c1117")
    ax_position.set_facecolor("#0c1117")

    # Draw Candles
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

    # Draw Position Builder Histogram
    p_width = (3 / (24 * 60)) * 0.78
    values = df["position_builder_scaled"]
    colors = np.where(values >= 0, "#12665f", "#713437")
    ax_position.bar(
        df["timestamp"],
        values,
        width=p_width,
        color=colors,
        edgecolor=colors,
        linewidth=0,
    )
    ax_position.axhline(0, linewidth=0.8, color="#30343b")

    # Labels & Aesthetics
    last_price = df["close"].iloc[-1]
    ax_price.set_title(
        f"NIFTY 50   |   3m   |   Last: {last_price:.2f}",
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
        f"OI Source: {future_symbol}",
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
if st.button("Run Position Builder"):
    if not ACCESS_TOKEN:
        st.error("Please provide a valid Upstox Access Token.")
        st.stop()

    try:
        with st.spinner("Fetching NIFTY Index and Futures data..."):
            idx_df = filter_market_hours(get_nifty_index_intraday())
            fut_key, fut_symbol = find_nearest_nifty_future()
            fut_df = filter_market_hours(get_nifty_future_intraday(fut_key))

            builder_df = calculate_position_builder(idx_df, fut_df)

        st.success(f"Connected successfully to {fut_symbol}")
        render_chart(builder_df, fut_symbol)

        st.subheader("Recent Position Builder Data")
        st.dataframe(
            builder_df[
                [
                    "timestamp",
                    "close",
                    "future_oi",
                    "position_type",
                    "position_builder_scaled",
                ]
            ].tail(15),
            use_container_width=True,
        )

    except Exception as err:
        st.error(f"Execution Error: {str(err)}")
