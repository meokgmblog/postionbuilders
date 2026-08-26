import concurrent.futures
from datetime import datetime
import urllib.parse
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

# ==========================================
# PAGE CONFIG & MINIMAL STYLING
# ==========================================
st.set_page_config(layout="wide", page_title="Nifty 50 Chart", page_icon="📈")

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
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
            max-width: 100% !important;
        }
        
        div[data-testid="stVerticalBlock"] > div { gap: 0.1rem !important; }
        div[data-testid="column"] { padding: 0px 2px !important; }
        
        div[data-baseweb="select"] > div {
            background-color: #1e222d !important;
            border: 1px solid #363c4e !important;
            color: #d1d4dc !important;
            border-radius: 4px;
            min-height: 28px !important;
            height: 28px !important;
        }
        div[data-baseweb="select"] span {
            color: #d1d4dc !important;
            font-weight: 600 !important;
            font-size: 12px !important;
        }
        
        .metric-card {
            background-color: #131722;
            border: 1px solid #2a2e39;
            border-radius: 4px;
            padding: 4px 10px;
            margin-bottom: 2px;
        }
        .metric-label {
            color: #787b86;
            font-size: 9px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .metric-val {
            font-size: 15px;
            font-weight: 700;
            color: #d1d4dc;
        }
    </style>
""",
    unsafe_allow_html=True,
)

UPSTOX_TOKEN = "YOUR_UPSTOX_TOKEN_HERE"
SPOT_KEY = "NSE_INDEX|Nifty 50"

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(
    {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }
)

# ==========================================
# FETCH ENGINE
# ==========================================
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
                columns=["timestamp", "open", "high", "low", "close", "vol", "oi"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
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

def get_expiry_dates(spot_key):
    encoded_key = urllib.parse.quote(spot_key)
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={encoded_key}"
    try:
        res = HTTP_SESSION.get(url, timeout=5)
        if res.status_code == 200:
            expiries = sorted(list({item["expiry"] for item in res.json().get("data", []) if "expiry" in item}))
            if expiries:
                return expiries
    except Exception:
        pass
    return [datetime.now().strftime("%Y-%m-%d")]

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
            # Cumulative intraday OI change relative to Market Open (09:15 AM)
            base_oi = df_res["oi"].iloc[0]
            df_res["cum_oi_change"] = df_res["oi"] - base_oi
            return df_res[["timestamp", "cum_oi_change"]]
        return pd.DataFrame()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, k) for k in keys]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if not res.empty:
                results.append(res)

    if results:
        return pd.concat(results).groupby("timestamp", as_index=False)["cum_oi_change"].sum()
    return pd.DataFrame()

def build_dataset(timeframe, num_strikes, selected_expiry):
    df_spot_1m = fetch_raw_1min_candles(SPOT_KEY)
    if df_spot_1m.empty:
        return pd.DataFrame()

    df_spot = resample_candles(df_spot_1m, timeframe)
    chain = fetch_option_chain(SPOT_KEY, selected_expiry)

    if chain:
        chain = sorted(chain, key=lambda x: x.get("strike_price", 0))
        spot_price = df_spot["close"].iloc[-1]
        closest_idx = min(range(len(chain)), key=lambda i: abs(chain[i]["strike_price"] - spot_price))
        
        selected = chain[max(0, closest_idx - num_strikes): min(len(chain), closest_idx + num_strikes + 1)]
        call_keys = [item["call_options"]["instrument_key"] for item in selected if "call_options" in item]
        put_keys = [item["put_options"]["instrument_key"] for item in selected if "put_options" in item]

        df_calls = fetch_strike_oi_parallel(call_keys, timeframe)
        df_puts = fetch_strike_oi_parallel(put_keys, timeframe)

        if not df_calls.empty and not df_puts.empty:
            merged = pd.merge(df_spot, df_calls.rename(columns={"cum_oi_change": "call_cum_oi"}), on="timestamp", how="left")
            merged = pd.merge(merged, df_puts.rename(columns={"cum_oi_change": "put_cum_oi"}), on="timestamp", how="left")
            merged["call_cum_oi"] = merged["call_cum_oi"].fillna(0)
            merged["put_cum_oi"] = merged["put_cum_oi"].fillna(0)
            
            # Position Builder = Put OI Change - Call OI Change
            raw_pb = merged["put_cum_oi"] - merged["call_cum_oi"]
            
            # Scale to [-200, 200]
            max_val = raw_pb.abs().max()
            merged["pos_builder"] = (raw_pb / max_val * 180.0) if max_val > 0 else raw_pb
        else:
            merged = df_spot.copy()
            merged["pos_builder"] = (merged["close"] - merged["open"]) * 10
    else:
        merged = df_spot.copy()
        merged["pos_builder"] = (merged["close"] - merged["open"]) * 10

    merged["color"] = merged["pos_builder"].apply(lambda x: "#089981" if x >= 0 else "#f23645")
    return merged

# ==========================================
# HEADER CONTROLS
# ==========================================
top_bar = st.container()
with top_bar:
    c1, c2, c3, c4, _ = st.columns([2.5, 1.2, 1.2, 2.0, 3.1])
    with c1:
        st.markdown("<h4 style='margin:0; font-size:16px; color:#f0f3fa;'>NIFTY 50</h4>", unsafe_allow_html=True)
    with c2:
        tf_option = st.selectbox("TF", options=["3min", "5min"], index=0, key="tf_s", label_visibility="collapsed")
    with c3:
        strike_count = st.selectbox("Strikes", options=[3, 5, 10], index=0, key="st_s", label_visibility="collapsed")
    with c4:
        expiry_input = st.selectbox("Expiry", options=get_expiry_dates(SPOT_KEY), index=0, key="ex_s", label_visibility="collapsed")

# ==========================================
# DASHBOARD METRICS & PLOT
# ==========================================
df = build_dataset(tf_option, strike_count, expiry_input)

if not df.empty:
    last_row = df.iloc[-1]
    
    # Net Delta & Bias matching rules
    current_net_delta = int(last_row['pos_builder'])
    bias_label = "BULLISH" if current_net_delta >= 0 else "BEARISH"
    bias_color = "#089981" if current_net_delta >= 0 else "#f23645"

    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            f"""<div class='metric-card'><div class='metric-label'>Spot Price</div><div class='metric-val'>{last_row['close']:.2f}</div></div>""",
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f"""<div class='metric-card'><div class='metric-label'>Net Position Delta</div><div class='metric-val' style='color:{last_row["color"]};'>{current_net_delta:+}</div></div>""",
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f"""<div class='metric-card'><div class='metric-label'>OI Bias</div><div class='metric-val' style='color:{bias_color};'>{bias_label}</div></div>""",
            unsafe_allow_html=True,
        )

    # Subplot Chart Initialization
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.0,
        row_heights=[0.72, 0.28],
    )

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
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["pos_builder"],
            marker_color=df["color"],
            marker_line_width=0,
            name="Position Builder",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        margin=dict(l=10, r=50, t=10, b=10),
        height=560,
        dragmode="pan",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        showlegend=False,
    )

    # Shared continuous crosshair configuration
    fig.update_xaxes(
        showgrid=True,
        gridcolor="#21262d",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#ffffff",
        spikethickness=1,
        spikedash="dash",
    )

    fig.update_yaxes(
        showgrid=True, gridcolor="#21262d", side="right", tickfont=dict(color="#8b949e", size=10), row=1, col=1
    )
    fig.update_yaxes(
        range=[-220, 220], showgrid=True, gridcolor="#21262d", zeroline=True, zerolinecolor="#30363d", side="right", tickfont=dict(color="#8b949e", size=10), row=2, col=1
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": True,
            "scrollZoom": True,
            "displaylogo": False,
        },
    )
else:
    st.error("Market data unavailable.")
