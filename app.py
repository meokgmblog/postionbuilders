import concurrent.futures
from datetime import datetime
import urllib.parse
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(
    layout="wide",
    page_title="Nifty 50 Chart",
    page_icon="📈",
)

# ==========================================
# CSS: INJECT REAL TRADINGVIEW CROSSHAIR
# ==========================================
st.markdown(
    """
    <style>
        html, body, [data-testid="stAppViewContainer"] {
            overflow: hidden !important;
            background-color: #0d1117;
            color: #d1d4dc;
        }
        .stApp { background-color: #0d1117; }
        
        div.block-container {
            padding-top: 0.1rem !important;
            padding-bottom: 0rem !important;
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
            max-width: 100% !important;
        }
        
        /* Metric cards styling */
        .metric-card {
            background-color: #131722;
            border: 1px solid #2a2e39;
            border-radius: 4px;
            padding: 4px 10px;
            margin-bottom: 4px;
        }
        .metric-label {
            color: #787b86;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .metric-val {
            font-size: 15px;
            font-weight: 700;
            color: #d1d4dc;
        }

        /* Force unified vertical crosshair across the canvas */
        .js-plotly-plot .plotly .spikeline {
            stroke: #787b86 !important;
            stroke-width: 1px !important;
            stroke-dasharray: 3px, 3px !important;
        }
    </style>
""",
    unsafe_allow_html=True,
)

UPSTOX_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2M0FZSEUiLCJqdGkiOiI2YThkNTc1Y2Y4MTJmNjA0MzcxZDNlM2MiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzY0NzgzNiwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3Njk1MjAwfQ.Z4zP9w3MecFeZEcX5sUt4YdhxS6skp25fbKOv8-_gPU"
SPOT_KEY = "NSE_INDEX|Nifty 50"

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(
    {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }
)


def fetch_raw_1min_candles(instrument_key):
    encoded_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{encoded_key}/1minute"
    try:
        res = HTTP_SESSION.get(url, timeout=6)
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
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(
                None
            )
            return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()


def resample_candles(df, timeframe="3min"):
    if df.empty:
        return pd.DataFrame()
    tf = "3min" if "3" in timeframe else "5min"
    return (
        df.set_index("timestamp")
        .resample(tf, origin="start_day", closed="left", label="left")
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


def fetch_option_chain(spot_key, expiry_date):
    encoded_key = urllib.parse.quote(spot_key)
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={encoded_key}&expiry_date={expiry_date}"
    try:
        res = HTTP_SESSION.get(url, timeout=5)
        if res.status_code == 200:
            return res.json().get("data", [])
    except Exception:
        pass
    return []


def fetch_strike_oi_parallel(keys, timeframe):
    def worker(key):
        df_1m = fetch_raw_1min_candles(key)
        if not df_1m.empty:
            df_res = resample_candles(df_1m, timeframe)
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
        return (
            pd.concat(results)
            .groupby("timestamp", as_index=False)["oi_diff"]
            .sum()
        )
    return pd.DataFrame()


def get_data(timeframe, num_strikes):
    df_spot = resample_candles(fetch_raw_1min_candles(SPOT_KEY), timeframe)
    if df_spot.empty:
        return pd.DataFrame()

    # Default fallback calculation if chain fails
    chain = fetch_option_chain(SPOT_KEY, datetime.now().strftime("%Y-%m-%d"))
    if chain:
        spot_price = df_spot["close"].iloc[-1]
        chain_sorted = sorted(chain, key=lambda x: x.get("strike_price", 0))
        closest_idx = min(
            range(len(chain_sorted)),
            key=lambda i: abs(chain_sorted[i]["strike_price"] - spot_price),
        )
        selected = chain_sorted[
            max(0, closest_idx - num_strikes) : min(
                len(chain_sorted), closest_idx + num_strikes + 1
            )
        ]

        c_keys = [
            i["call_options"]["instrument_key"]
            for i in selected
            if "call_options" in i
        ]
        p_keys = [
            i["put_options"]["instrument_key"]
            for i in selected
            if "put_options" in i
        ]

        df_c = fetch_strike_oi_parallel(c_keys, timeframe)
        df_p = fetch_strike_oi_parallel(p_keys, timeframe)

        if not df_c.empty and not df_p.empty:
            merged = pd.merge(
                df_spot,
                df_c.rename(columns={"oi_diff": "c_oi"}),
                on="timestamp",
                how="left",
            )
            merged = pd.merge(
                merged,
                df_p.rename(columns={"oi_diff": "p_oi"}),
                on="timestamp",
                how="left",
            )
            raw = merged["p_oi"].fillna(0) - merged["c_oi"].fillna(0)
            max_val = raw.abs().max()
            merged["pos_builder"] = (
                (raw / max_val) * 180.0 if max_val > 0 else raw
            )
        else:
            df_spot["pos_builder"] = 0
            merged = df_spot
    else:
        df_spot["pos_builder"] = (df_spot["close"] - df_spot["open"]) * 10
        merged = df_spot

    merged["color"] = merged["pos_builder"].apply(
        lambda x: "#089981" if x >= 0 else "#f23645"
    )
    return merged


# ==========================================
# MAIN INTERFACE & RENDER
# ==========================================
df = get_data("3min", 3)

if not df.empty:
    last = df.iloc[-1]

    # TOP METRICS (Stripped High/Low as requested)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            f"<div class='metric-card'><div class='metric-label'>Spot Price</div><div class='metric-val'>{last['close']:.2f}</div></div>",
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f"<div class='metric-card'><div class='metric-label'>Net Position Delta</div><div class='metric-val' style='color:{last['color']};'>{last['pos_builder']:,.0f}</div></div>",
            unsafe_allow_html=True,
        )
    with col3:
        bias = "BULLISH" if last["pos_builder"] >= 0 else "BEARISH"
        st.markdown(
            f"<div class='metric-card'><div class='metric-label'>OI Bias</div><div class='metric-val' style='color:{last['color']};'>{bias}</div></div>",
            unsafe_allow_html=True,
        )

    # SUBPLOT SETUP WITH SHARED X-AXIS
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.0,
        row_heights=[0.7, 0.3],
    )

    # Candlestick (Hover info stripped)
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Nifty 50",
            increasing_line_color="#089981",
            decreasing_line_color="#f23645",
            increasing_fillcolor="#089981",
            decreasing_fillcolor="#f23645",
            hoverinfo="x",  # Removes OHLC text overlay
        ),
        row=1,
        col=1,
    )

    # Histogram
    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["pos_builder"],
            marker_color=df["color"],
            name="Position Builder",
            hoverinfo="x+y",
        ),
        row=2,
        col=1,
    )

    # LAYOUT AND FULL CROSSHAIR OVERLAY
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        margin=dict(l=10, r=40, t=10, b=10),
        height=580,
        hovermode="x",  # Single vertical crosshair mode
        showlegend=False,
        xaxis_rangeslider_visible=False,
    )

    # Synchronize Spikes for Vertical Line Linking
    spike_config = dict(
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#8b949e",
        spikethickness=1,
        spikedash="dash",
        showgrid=True,
        gridcolor="#21262d",
    )

    fig.update_xaxes(**spike_config)
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#21262d",
        side="right",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        range=[-200, 200],
        showgrid=True,
        gridcolor="#21262d",
        zerolinecolor="#30363d",
        side="right",
        row=2,
        col=1,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": False,
            "scrollZoom": True,
        },
    )
