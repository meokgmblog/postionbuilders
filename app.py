import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from datetime import datetime, date
from urllib.parse import quote


# ================================================================
# CONFIGURATION
# ================================================================

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"

# NIFTY 50 index
NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"

# Chart timeframe
INTERVAL = 3

# Session
MARKET_START = "09:15"
MARKET_END   = "15:30"

# Position Builder settings
OI_LOOKBACK = 1
NORMALIZE_OI = True
USE_OI_PERCENT = True
HISTOGRAM_SCALE = 1000
MIN_OI_CHANGE = 0

# Display
FIG_WIDTH = 16
FIG_HEIGHT = 8
SHOW_GRID = False

# ================================================================
# UPSTOX HEADERS
# ================================================================

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}


# ================================================================
# 1. GENERIC UPSTOX GET
# ================================================================

def upstox_get(url, params=None):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=20
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"NETWORK ERROR: {str(e)}")

    print("\n================ UPSTOX DEBUG ================")
    print("HTTP STATUS:", response.status_code)
    print("URL:", url)
    print("PARAMS:", params)
    print("RESPONSE:", response.text[:500])
    print("===============================================\n")

    if response.status_code != 200:
        raise RuntimeError(
            f"UPSTOX HTTP {response.status_code}: {response.text[:1000]}"
        )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError("Upstox returned non-JSON response.")

    if data.get("status") != "success":
        raise RuntimeError(f"UPSTOX API ERROR: {data}")

    return data


# ================================================================
# 2. GET NIFTY INDEX 3-MINUTE DATA
# ================================================================

def get_nifty_index_intraday():
    encoded_key = quote(NIFTY_INDEX_KEY, safe="")
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

    print("\nFetching NIFTY 50 index data...")
    response = upstox_get(url)
    candles = response["data"]["candles"]

    if not candles:
        raise RuntimeError("No NIFTY index candles returned.")

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


# ================================================================
# 3. SEARCH NIFTY FUTURES
# ================================================================

def find_nearest_nifty_future():
    # Corrected Upstox v2 Search API endpoint parameter key (`search_text`)
    url = "https://api.upstox.com/v2/option/contract"
    params = {
        "instrument_key": NIFTY_INDEX_KEY
    }

    print("\nSearching active NIFTY Futures via Option Contract API...")
    
    try:
        # Fallback to general search if contract chain fails
        url_search = "https://api.upstox.com/v2/instruments/search"
        search_params = {
            "search_text": "NIFTY FUT",
            "exchange": "NSE_FO"
        }
        
        res = requests.get(
            url_search,
            headers=HEADERS,
            params=search_params,
            timeout=20
        )
        
        result = res.json()
        instruments = result.get("data", [])
        
    except Exception as e:
        raise RuntimeError(f"UPSTOX CONNECTION ERROR: {e}")

    valid = []
    today = pd.Timestamp(datetime.now().date())

    for item in instruments:
        # Filter strictly for Futures
        if item.get("instrument_type") != "FUT":
            continue

        expiry = item.get("expiry")
        if not expiry:
            continue

        try:
            expiry_date = pd.Timestamp(expiry)
        except Exception:
            continue

        if expiry_date.date() < today.date():
            continue

        instrument_key = item.get("instrument_key")
        if not instrument_key:
            continue

        valid.append({
            "instrument_key": instrument_key,
            "trading_symbol": item.get("trading_symbol"),
            "expiry": expiry_date,
            "segment": item.get("segment"),
            "instrument_type": item.get("instrument_type"),
            "underlying_symbol": item.get("underlying_symbol"),
            "lot_size": item.get("lot_size"),
        })

    if not valid:
        raise RuntimeError(
            "Upstox returned search results, but none had a valid future expiry."
        )

    futures = pd.DataFrame(valid).sort_values("expiry").reset_index(drop=True)
    selected = futures.iloc[0]

    print("\nSELECTED FUTURE:")
    print("Trading Symbol :", selected["trading_symbol"])
    print("Expiry         :", selected["expiry"].strftime("%Y-%m-%d"))
    print("Instrument Key :", selected["instrument_key"])

    return selected["instrument_key"]


# ================================================================
# 4. GET NIFTY FUTURES 3-MINUTE DATA
# ================================================================

def get_nifty_future_intraday(instrument_key):
    encoded_key = quote(instrument_key, safe="")
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{encoded_key}/minutes/{INTERVAL}"

    print("\nFetching NIFTY FUTURES data...")
    response = upstox_get(url)
    candles = response["data"]["candles"]

    if not candles:
        raise RuntimeError("No NIFTY futures candles returned.")

    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


# ================================================================
# 5. FILTER MARKET HOURS
# ================================================================

def filter_market_hours(df):
    df = df.copy()
    df["time"] = df["timestamp"].dt.time

    start = datetime.strptime(MARKET_START, "%H:%M").time()
    end = datetime.strptime(MARKET_END, "%H:%M").time()

    df = df[(df["time"] >= start) & (df["time"] <= end)].copy()
    df.drop(columns=["time"], inplace=True)
    return df.reset_index(drop=True)


# ================================================================
# 6. CALCULATE POSITION BUILDER
# ================================================================

def calculate_position_builder(price_df, future_df):
    price = price_df.copy()
    future = future_df[["timestamp", "close", "oi"]].copy()

    future.rename(
        columns={"close": "future_close", "oi": "future_oi"},
        inplace=True
    )

    df = pd.merge(price, future, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)

    if df.empty:
        raise RuntimeError("No matching timestamps between Index and Futures.")

    df["price_change"] = df["close"].diff(OI_LOOKBACK)
    df["price_change_pct"] = df["close"].pct_change(OI_LOOKBACK) * 100

    df["oi_change"] = df["future_oi"].diff(OI_LOOKBACK)
    df["oi_change_pct"] = df["future_oi"].pct_change(OI_LOOKBACK) * 100

    def classify(row):
        p_chg = row["price_change"]
        oi_chg = row["oi_change"]

        if pd.isna(p_chg) or pd.isna(oi_chg) or abs(oi_chg) <= MIN_OI_CHANGE:
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

    if USE_OI_PERCENT:
        df["position_builder"] = df["oi_change_pct"]
    else:
        df["position_builder"] = df["oi_change"]

    bullish = ["LONG BUILDUP", "SHORT COVERING"]
    bearish = ["SHORT BUILDUP", "LONG UNWINDING"]

    df.loc[df["position_type"].isin(bullish), "position_builder"] = df["position_builder"].abs()
    df.loc[df["position_type"].isin(bearish), "position_builder"] = -df["position_builder"].abs()
    df.loc[df["position_type"] == "NEUTRAL", "position_builder"] = 0

    df["position_builder_scaled"] = df["position_builder"] * HISTOGRAM_SCALE
    return df


# ================================================================
# 7. DRAW CANDLES & HISTOGRAM
# ================================================================

def draw_candles(ax, df):
    width = (3 / (24 * 60)) * 0.75
    for _, row in df.iterrows():
        t, o, h, l, c = row["timestamp"], row["open"], row["high"], row["low"], row["close"]
        color = "#19b5a5" if c >= o else "#ff4d5a"
        
        ax.plot([t, t], [l, h], color=color, linewidth=0.8, zorder=2)
        bottom = min(o, c)
        height = max(abs(c - o), df["close"].mean() * 0.00002)
        ax.bar(t, height, bottom=bottom, width=width, color=color, edgecolor=color, linewidth=0, zorder=3)


def draw_position_builder(ax, df):
    width = (3 / (24 * 60)) * 0.78
    values = df["position_builder_scaled"]
    colors = np.where(values >= 0, "#12665f", "#713437")

    ax.bar(df["timestamp"], values, width=width, color=colors, edgecolor=colors, linewidth=0)
    ax.axhline(0, linewidth=0.8, color="#30343b")


def plot_chart(df, future_symbol):
    plt.close("all")
    fig = plt.figure(figsize=(FIG_WIDTH, FIG_HEIGHT), facecolor="#0c1117")
    gs = fig.add_gridspec(5, 1, hspace=0.04)

    ax_price = fig.add_subplot(gs[:4, 0])
    ax_position = fig.add_subplot(gs[4, 0], sharex=ax_price)

    ax_price.set_facecolor("#0c1117")
    ax_position.set_facecolor("#0c1117")

    draw_candles(ax_price, df)
    draw_position_builder(ax_position, df)

    last_price = df["close"].iloc[-1]
    ax_price.set_title(
        f"NIFTY 50   |   3m   |   Last: {last_price:.2f}",
        loc="left", fontsize=15, fontweight="bold", color="white", pad=12
    )

    ax_position.text(0.005, 0.90, "POSITION BUILDERS", transform=ax_position.transAxes, fontsize=9, fontweight="bold", color="#b8c0cc", va="top")
    ax_price.text(0.995, 0.97, f"OI Source: {future_symbol}", transform=ax_price.transAxes, ha="right", va="top", fontsize=8, color="#9aa4b2")

    for ax in [ax_price, ax_position]:
        for spine in ax.spines.values():
            spine.set_visible(False)

    ax_position.xaxis.set_major_locator(mdates.MinuteLocator(interval=30))
    ax_position.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    plt.show()


# ================================================================
# 8. MAIN EXECUTION
# ================================================================

def main():
    print("\n=================================================")
    print("     NIFTY 50 - 3 MINUTE POSITION BUILDER        ")
    print("=================================================")

    if not ACCESS_TOKEN or ACCESS_TOKEN == "YOUR_UPSTOX_ACCESS_TOKEN":
        raise RuntimeError("Please enter a valid Upstox ACCESS_TOKEN.")

    index_df = filter_market_hours(get_nifty_index_intraday())
    future_key = find_nearest_nifty_future()
    future_df = filter_market_hours(get_nifty_future_intraday(future_key))

    df = calculate_position_builder(index_df, future_df)

    print("\nLATEST POSITION BUILDER DATA:")
    print(df[["timestamp", "close", "future_oi", "position_type", "position_builder_scaled"]].tail(10).to_string(index=False))

    plot_chart(df, future_key)


if __name__ == "__main__":
    main()
