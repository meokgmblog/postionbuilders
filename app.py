# ================================================================
# NIFTY 50 - 3 MINUTE POSITION BUILDER CHART
# ================================================================
#
# TOP PANEL:
#     NIFTY 50 INDEX 3-minute candlestick chart
#
# LOWER PANEL:
#     Position Builder / OI-based positioning histogram
#
# DATA:
#     Upstox API V3
#
# TOP:
#     NSE_INDEX|Nifty 50
#
# OI:
#     Nearest NIFTY FUTURES contract
#
# ================================================================

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

# ------------------------------------------------
# Chart timeframe
# ------------------------------------------------

INTERVAL = 3

# ------------------------------------------------
# Session
# ------------------------------------------------

MARKET_START = "09:15"
MARKET_END   = "15:30"

# ------------------------------------------------
# Position Builder settings
# ------------------------------------------------

OI_LOOKBACK = 1

# Normalize OI change?
NORMALIZE_OI = True

# Use percentage OI change rather than raw OI change
USE_OI_PERCENT = True

# Scale the histogram
HISTOGRAM_SCALE = 1000

# Minimum OI change filter
MIN_OI_CHANGE = 0

# ------------------------------------------------
# Display
# ------------------------------------------------

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
        raise RuntimeError(
            f"NETWORK ERROR: {str(e)}"
        )

    # Do NOT expose access token
    safe_url = url

    print("\n================ UPSTOX DEBUG ================")
    print("HTTP STATUS:", response.status_code)
    print("URL:", safe_url)
    print("PARAMS:", params)
    print("RESPONSE:", response.text[:2000])
    print("===============================================\n")

    if response.status_code != 200:

        raise RuntimeError(
            f"UPSTOX HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "Upstox returned non-JSON response."
        )

    if data.get("status") != "success":

        raise RuntimeError(
            f"UPSTOX API ERROR: {data}"
        )

    return data

    # ------------------------------------------------------------
    # ERROR
    # ------------------------------------------------------------

    try:

        error_data = response.json()

    except Exception:

        error_data = response.text

    raise RuntimeError(
        f"\n"
        f"Upstox API Error\n"
        f"HTTP Status : {response.status_code}\n"
        f"URL         : {url}\n"
        f"Response    : {error_data}"
    )


# ================================================================
# 2. GET NIFTY INDEX 3-MINUTE DATA
# ================================================================

def get_nifty_index_intraday():

    encoded_key = quote(
        NIFTY_INDEX_KEY,
        safe=""
    )

    url = (
        f"https://api.upstox.com/v3/"
        f"historical-candle/intraday/"
        f"{encoded_key}/minutes/{INTERVAL}"
    )

    print("\nFetching NIFTY 50 index data...")
    print("Instrument:", NIFTY_INDEX_KEY)
    print("Interval :", f"{INTERVAL} minutes")

    response = upstox_get(url)

    candles = response["data"]["candles"]

    if not candles:
        raise RuntimeError(
            "No NIFTY index candles returned."
        )

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "oi"
        ]
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    return df


# ================================================================
# 3. SEARCH NIFTY FUTURES
# ================================================================
#
# Upstox has an Instrument Search API.
#
# We search:
#
#     NIFTY
#     NSE_FO
#     FUT
#
# Then select the nearest valid expiry.
#
# ================================================================

def main():

    st.title("NIFTY Position Builder")

    if not ACCESS_TOKEN:

        st.error(
            "UPSTOX_ACCESS_TOKEN is missing."
        )

        st.stop()

    st.write(
        "Access token loaded:",
        bool(ACCESS_TOKEN)
    )

    # DO NOT print the actual token

    future_key = find_nearest_nifty_future()

    st.success(
        f"NIFTY FUTURE FOUND: {future_key}"
    )

def find_nearest_nifty_future():

    url = "https://api.upstox.com/v2/instruments/search"

    params = {
        "query": "NIFTY",
        "exchanges": "NSE",
        "segments": "FO",
        "instrument_types": "FUT",
        "page_number": 1,
        "records": 30,
    }

    try:

        response = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ACCESS_TOKEN}",
            },
            params=params,
            timeout=20,
        )

    except Exception as e:

        raise RuntimeError(
            f"Could not connect to Upstox: {e}"
        )

    # ----------------------------------------------------------
    # IMPORTANT
    # Show status, but NEVER show access token
    # ----------------------------------------------------------

    status = response.status_code

    try:
        body = response.json()
    except Exception:
        body = response.text[:2000]

    print("======================================")
    print("UPSTOX INSTRUMENT SEARCH")
    print("HTTP STATUS:", status)
    print("RESPONSE:", body)
    print("======================================")

    if status != 200:

        raise RuntimeError(
            f"Upstox Instrument Search failed. "
            f"HTTP {status}. "
            f"Response: {body}"
        )

    if body.get("status") != "success":

        raise RuntimeError(
            f"Upstox returned unsuccessful response: {body}"
        )

    instruments = body.get("data", [])

    if not instruments:

        raise RuntimeError(
            "Upstox returned zero NIFTY futures."
        )

    # ----------------------------------------------------------
    # FILTER NIFTY FUTURES
    # ----------------------------------------------------------

    futures = []

    today = pd.Timestamp(
        datetime.now().date()
    )

    for item in instruments:

        if str(
            item.get("instrument_type", "")
        ).upper() != "FUT":
            continue

        if str(
            item.get("segment", "")
        ).upper() != "NSE_FO":
            continue

        underlying = str(
            item.get("underlying_symbol", "")
        ).upper()

        if underlying != "NIFTY":
            continue

        expiry = item.get("expiry")

        if not expiry:
            continue

        expiry_date = pd.Timestamp(expiry)

        if expiry_date.date() < today.date():
            continue

        futures.append({
            "instrument_key":
                item.get("instrument_key"),

            "trading_symbol":
                item.get("trading_symbol"),

            "expiry":
                expiry_date,

            "lot_size":
                item.get("lot_size"),

            "underlying_symbol":
                item.get("underlying_symbol"),
        })

    if not futures:

        raise RuntimeError(
            "Upstox responded successfully, "
            "but no active NIFTY FUT contract was found."
        )

    futures_df = pd.DataFrame(futures)

    futures_df = futures_df.sort_values(
        "expiry"
    ).reset_index(drop=True)

    print("\nAVAILABLE NIFTY FUTURES")
    print(futures_df.to_string(index=False))

    selected = futures_df.iloc[0]

    print("\nSELECTED CONTRACT")
    print("-------------------------------")
    print(
        "Symbol:",
        selected["trading_symbol"]
    )
    print(
        "Expiry:",
        selected["expiry"].date()
    )
    print(
        "Key:",
        selected["instrument_key"]
    )
    print("-------------------------------")

    return selected["instrument_key"]


# ================================================================
# 4. GET NIFTY FUTURES 3-MINUTE DATA
# ================================================================

def get_nifty_future_intraday(
    instrument_key
):

    encoded_key = quote(
        instrument_key,
        safe=""
    )

    url = (
        f"https://api.upstox.com/v3/"
        f"historical-candle/intraday/"
        f"{encoded_key}/minutes/{INTERVAL}"
    )

    print("\nFetching NIFTY FUTURES data...")
    print("Instrument:", instrument_key)

    response = upstox_get(url)

    candles = response["data"]["candles"]

    if not candles:
        raise RuntimeError(
            "No NIFTY futures candles returned."
        )

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "oi"
        ]
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    return df


# ================================================================
# 5. FILTER MARKET HOURS
# ================================================================

def filter_market_hours(df):

    df = df.copy()

    df["time"] = df[
        "timestamp"
    ].dt.time

    start = datetime.strptime(
        MARKET_START,
        "%H:%M"
    ).time()

    end = datetime.strptime(
        MARKET_END,
        "%H:%M"
    ).time()

    df = df[
        (df["time"] >= start)
        &
        (df["time"] <= end)
    ].copy()

    df.drop(
        columns=["time"],
        inplace=True
    )

    return df.reset_index(
        drop=True
    )


# ================================================================
# 6. CALCULATE POSITION BUILDER
# ================================================================
#
# This is the main calculation.
#
# PRICE CHANGE:
#
#     current close - previous close
#
# OI CHANGE:
#
#     current OI - previous OI
#
# CLASSIFICATION:
#
#     price + / OI + = LONG BUILDUP
#     price - / OI + = SHORT BUILDUP
#     price + / OI - = SHORT COVERING
#     price - / OI - = LONG UNWINDING
#
# For the histogram:
#
#     bullish positioning  -> positive
#     bearish positioning  -> negative
#
# ================================================================

def calculate_position_builder(
    price_df,
    future_df
):

    price = price_df.copy()
    future = future_df.copy()

    # ------------------------------------------------------------
    # Rename futures fields
    # ------------------------------------------------------------

    future = future[
        [
            "timestamp",
            "close",
            "oi"
        ]
    ].copy()

    future.rename(
        columns={
            "close": "future_close",
            "oi": "future_oi"
        },
        inplace=True
    )

    # ------------------------------------------------------------
    # Merge on exact 3-minute timestamp
    # ------------------------------------------------------------

    df = pd.merge(
        price,
        future,
        on="timestamp",
        how="inner"
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    if df.empty:

        raise RuntimeError(
            "No matching timestamps between "
            "NIFTY index and NIFTY futures."
        )

    # ------------------------------------------------------------
    # PRICE CHANGE
    # ------------------------------------------------------------

    df["price_change"] = (
        df["close"]
        .diff(OI_LOOKBACK)
    )

    df["price_change_pct"] = (
        df["close"]
        .pct_change(OI_LOOKBACK)
        * 100
    )

    # ------------------------------------------------------------
    # OI CHANGE
    # ------------------------------------------------------------

    df["oi_change"] = (
        df["future_oi"]
        .diff(OI_LOOKBACK)
    )

    df["oi_change_pct"] = (
        df["future_oi"]
        .pct_change(OI_LOOKBACK)
        * 100
    )

    # ------------------------------------------------------------
    # OI CHANGE ABSOLUTE
    # ------------------------------------------------------------

    df["oi_change_abs"] = (
        df["oi_change"].abs()
    )

    # ------------------------------------------------------------
    # POSITION CLASSIFICATION
    # ------------------------------------------------------------

    def classify(row):

        price_change = row[
            "price_change"
        ]

        oi_change = row[
            "oi_change"
        ]

        if pd.isna(price_change):
            return "NEUTRAL"

        if pd.isna(oi_change):
            return "NEUTRAL"

        if abs(oi_change) <= MIN_OI_CHANGE:
            return "NEUTRAL"

        # ------------------------------------------
        # PRICE UP + OI UP
        # ------------------------------------------

        if (
            price_change > 0
            and oi_change > 0
        ):
            return "LONG BUILDUP"

        # ------------------------------------------
        # PRICE DOWN + OI UP
        # ------------------------------------------

        if (
            price_change < 0
            and oi_change > 0
        ):
            return "SHORT BUILDUP"

        # ------------------------------------------
        # PRICE UP + OI DOWN
        # ------------------------------------------

        if (
            price_change > 0
            and oi_change < 0
        ):
            return "SHORT COVERING"

        # ------------------------------------------
        # PRICE DOWN + OI DOWN
        # ------------------------------------------

        if (
            price_change < 0
            and oi_change < 0
        ):
            return "LONG UNWINDING"

        return "NEUTRAL"

    df["position_type"] = df.apply(
        classify,
        axis=1
    )

    # ============================================================
    # POSITION BUILDER HISTOGRAM
    # ============================================================
    #
    # Version A:
    #
    # positive = bullish positioning
    # negative = bearish positioning
    #
    # Magnitude = percentage OI change
    #
    # ============================================================

    if USE_OI_PERCENT:

        df["position_builder"] = (
            df["oi_change_pct"]
        )

    else:

        df["position_builder"] = (
            df["oi_change"]
        )

    # ------------------------------------------------------------
    # Assign direction
    # ------------------------------------------------------------

    # LONG BUILDUP
    # SHORT COVERING
    #
    # => positive
    #
    # SHORT BUILDUP
    # LONG UNWINDING
    #
    # => negative

    bullish = [
        "LONG BUILDUP",
        "SHORT COVERING"
    ]

    bearish = [
        "SHORT BUILDUP",
        "LONG UNWINDING"
    ]

    df.loc[
        df["position_type"].isin(bullish),
        "position_builder"
    ] = (
        df.loc[
            df["position_type"].isin(bullish),
            "position_builder"
        ].abs()
    )

    df.loc[
        df["position_type"].isin(bearish),
        "position_builder"
    ] = -(
        df.loc[
            df["position_type"].isin(bearish),
            "position_builder"
        ].abs()
    )

    # Neutral = zero

    df.loc[
        df["position_type"] == "NEUTRAL",
        "position_builder"
    ] = 0

    # Scale for display

    df["position_builder_scaled"] = (
        df["position_builder"]
        * HISTOGRAM_SCALE
    )

    return df


# ================================================================
# 7. DRAW CANDLE
# ================================================================

def draw_candles(
    ax,
    df
):

    # Candle width for 3-minute bars
    width = (
        3
        / (24 * 60)
        * 0.75
    )

    for _, row in df.iterrows():

        t = row["timestamp"]

        o = row["open"]
        h = row["high"]
        l = row["low"]
        c = row["close"]

        # --------------------------------------------------------
        # Bullish
        # --------------------------------------------------------

        if c >= o:

            candle_color = "#19b5a5"

        # --------------------------------------------------------
        # Bearish
        # --------------------------------------------------------

        else:

            candle_color = "#ff4d5a"

        # --------------------------------------------------------
        # Wick
        # --------------------------------------------------------

        ax.plot(
            [t, t],
            [l, h],
            color=candle_color,
            linewidth=0.8,
            zorder=2
        )

        # --------------------------------------------------------
        # Body
        # --------------------------------------------------------

        bottom = min(o, c)

        height = abs(
            c - o
        )

        if height == 0:

            height = (
                df["close"].mean()
                * 0.00002
            )

        ax.bar(
            t,
            height,
            bottom=bottom,
            width=width,
            color=candle_color,
            edgecolor=candle_color,
            linewidth=0,
            zorder=3
        )


# ================================================================
# 8. DRAW POSITION BUILDER
# ================================================================

def draw_position_builder(
    ax,
    df
):

    width = (
        3
        / (24 * 60)
        * 0.78
    )

    values = (
        df[
            "position_builder_scaled"
        ]
    )

    # ------------------------------------------------------------
    # Green = bullish positioning
    # Red = bearish positioning
    # ------------------------------------------------------------

    colors = np.where(
        values >= 0,
        "#12665f",
        "#713437"
    )

    ax.bar(
        df["timestamp"],
        values,
        width=width,
        color=colors,
        edgecolor=colors,
        linewidth=0
    )

    # ------------------------------------------------------------
    # ZERO LINE
    # ------------------------------------------------------------

    ax.axhline(
        0,
        linewidth=0.8,
        color="#30343b"
    )


# ================================================================
# 9. COMPLETE CHART
# ================================================================

def plot_chart(
    df,
    future_symbol
):

    plt.close("all")

    fig = plt.figure(
        figsize=(
            FIG_WIDTH,
            FIG_HEIGHT
        ),
        facecolor="#0c1117"
    )

    gs = fig.add_gridspec(
        5,
        1,
        hspace=0.04
    )

    ax_price = fig.add_subplot(
        gs[:4, 0]
    )

    ax_position = fig.add_subplot(
        gs[4, 0],
        sharex=ax_price
    )

    # ============================================================
    # DARK BACKGROUND
    # ============================================================

    ax_price.set_facecolor(
        "#0c1117"
    )

    ax_position.set_facecolor(
        "#0c1117"
    )

    # ============================================================
    # PRICE
    # ============================================================

    draw_candles(
        ax_price,
        df
    )

    # ============================================================
    # POSITION BUILDER
    # ============================================================

    draw_position_builder(
        ax_position,
        df
    )

    # ============================================================
    # PRICE TITLE
    # ============================================================

    last_price = df[
        "close"
    ].iloc[-1]

    ax_price.set_title(
        f"NIFTY 50   |   3m   |   "
        f"Last: {last_price:.2f}",
        loc="left",
        fontsize=15,
        fontweight="bold",
        color="white",
        pad=12
    )

    # ============================================================
    # POSITION BUILDER TITLE
    # ============================================================

    ax_position.text(
        0.005,
        0.90,
        "POSITION BUILDERS",
        transform=ax_position.transAxes,
        fontsize=9,
        fontweight="bold",
        color="#b8c0cc",
        va="top"
    )

    # ============================================================
    # FUTURES INFO
    # ============================================================

    ax_price.text(
        0.995,
        0.97,
        f"OI Source: {future_symbol}",
        transform=ax_price.transAxes,
        ha="right",
        va="top",
        fontsize=8,
        color="#9aa4b2"
    )

    # ============================================================
    # AXIS
    # ============================================================

    ax_price.tick_params(
        axis="x",
        labelbottom=False,
        colors="#89929e"
    )

    ax_price.tick_params(
        axis="y",
        colors="#89929e",
        labelsize=8
    )

    ax_position.tick_params(
        axis="x",
        colors="#89929e",
        labelsize=9
    )

    ax_position.tick_params(
        axis="y",
        colors="#89929e",
        labelsize=8
    )

    # ============================================================
    # REMOVE SPINES
    # ============================================================

    for ax in [
        ax_price,
        ax_position
    ]:

        for spine in ax.spines.values():

            spine.set_visible(False)

    # ============================================================
    # TIME FORMAT
    # ============================================================

    ax_position.xaxis.set_major_locator(
        mdates.MinuteLocator(
            interval=30
        )
    )

    ax_position.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%-I:%M %p"
        )
    )

    # Windows can have issues with %-I.
    #
    # If needed replace the formatter with:
    #
    # mdates.DateFormatter("%H:%M")

    # ============================================================
    # GRID
    # ============================================================

    if SHOW_GRID:

        ax_price.grid(
            True,
            alpha=0.08
        )

        ax_position.grid(
            True,
            alpha=0.08
        )

    # ============================================================
    # LIMITS
    # ============================================================

    ax_price.margins(
        x=0.01,
        y=0.08
    )

    ax_position.margins(
        x=0.01,
        y=0.15
    )

    # ============================================================
    # SHOW
    # ============================================================

    plt.show()


# ================================================================
# 10. PRINT POSITION DATA
# ================================================================

def print_position_data(df):

    cols = [
        "timestamp",
        "close",
        "future_oi",
        "price_change_pct",
        "oi_change",
        "oi_change_pct",
        "position_type",
        "position_builder_scaled"
    ]

    output = df[cols].copy()

    print("\n")
    print("=" * 120)
    print("POSITION BUILDER DATA")
    print("=" * 120)

    print(
        output.tail(30).to_string(
            index=False
        )
    )

    print("=" * 120)


# ================================================================
# 11. SUMMARY
# ================================================================

def print_summary(df):

    print("\n")
    print("=" * 70)
    print("POSITION BUILDER SUMMARY")
    print("=" * 70)

    counts = (
        df["position_type"]
        .value_counts()
    )

    for name, count in counts.items():

        print(
            f"{name:<20} : {count}"
        )

    print("=" * 70)

    latest = df.iloc[-1]

    print(
        "\nLATEST POSITION"
    )

    print(
        "Time          :",
        latest["timestamp"]
    )

    print(
        "NIFTY Close   :",
        round(
            latest["close"],
            2
        )
    )

    print(
        "Futures OI    :",
        int(
            latest["future_oi"]
        )
    )

    print(
        "Price Change  :",
        round(
            latest["price_change_pct"],
            4
        ),
        "%"
    )

    print(
        "OI Change     :",
        int(
            latest["oi_change"]
        )
    )

    print(
        "OI Change %   :",
        round(
            latest["oi_change_pct"],
            4
        ),
        "%"
    )

    print(
        "Position      :",
        latest["position_type"]
    )

    print(
        "Builder Value :",
        round(
            latest["position_builder_scaled"],
            4
        )
    )

    print("=" * 70)


# ================================================================
# 12. SAVE CSV
# ================================================================

def save_csv(df):

    filename = (
        "nifty_position_builder_3m.csv"
    )

    df.to_csv(
        filename,
        index=False
    )

    print(
        f"\nSaved: {filename}"
    )


# ================================================================
# 13. MAIN
# ================================================================

def main():

    print("\n")
    print("=" * 80)
    print(" NIFTY 50 - 3 MINUTE POSITION BUILDER ")
    print("=" * 80)

    if (
        ACCESS_TOKEN == ""
        or
        ACCESS_TOKEN == "YOUR_UPSTOX_ACCESS_TOKEN"
    ):

        raise RuntimeError(
            "\nPlease put your Upstox ACCESS_TOKEN "
            "in ACCESS_TOKEN."
        )

    # ------------------------------------------------------------
    # INDEX
    # ------------------------------------------------------------

    index_df = (
        get_nifty_index_intraday()
    )

    index_df = (
        filter_market_hours(
            index_df
        )
    )

    # ------------------------------------------------------------
    # FUTURES
    # ------------------------------------------------------------

    future_key = (
        find_nearest_nifty_future()
    )

    future_df = (
        get_nifty_future_intraday(
            future_key
        )
    )

    future_df = (
        filter_market_hours(
            future_df
        )
    )

    # ------------------------------------------------------------
    # POSITION BUILDER
    # ------------------------------------------------------------

    df = calculate_position_builder(
        index_df,
        future_df
    )

    # ------------------------------------------------------------
    # PRINT
    # ------------------------------------------------------------

    print_position_data(
        df
    )

    print_summary(
        df
    )

    # ------------------------------------------------------------
    # SAVE
    # ------------------------------------------------------------

    save_csv(
        df
    )

    # ------------------------------------------------------------
    # CHART
    # ------------------------------------------------------------

    future_symbol = future_key

    plot_chart(
        df,
        future_symbol
    )


# ================================================================
# RUN
# ================================================================

if __name__ == "__main__":

    main()
