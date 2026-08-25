import concurrent.futures
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

# ==========================================
# PAGE CONFIG & OVERFLOW/DROPDOWN CSS FIXES
# ==========================================
st.set_page_config(
    layout="wide",
    page_title="Nifty 50 | TradingView Multi-Strike Apex",
    page_icon="📈",
)

st.markdown(
    """
    <style>
        html, body {
            overflow: hidden !important;
            background-color: #0b0e14;
            color: #d1d4dc;
        }
        
        .stApp {
            background-color: #0b0e14;
        }
        
        div.block-container {
            padding-top: 0.3rem !important;
            padding-bottom: 0rem !important;
            padding-left: 0.6rem !important;
            padding-right: 0.6rem !important;
            max-width: 100% !important;
        }
        
        div[data-testid="stVerticalBlock"] > div {
            gap: 0.15rem !important;
        }

        div[data-baseweb="popover"], div[data-baseweb="menu"] {
            z-index: 999999 !important;
        }
        
        div[data-baseweb="select"] > div {
            background-color: #161b22 !important;
            border: 1px solid #2d333b !important;
            color: #d1d4dc !important;
            border-radius: 6px;
            min-height: 32px !important;
            height: 32px !important;
        }
        
        div[data-baseweb="select"] span {
            color: #d1d4dc !important;
            font-weight: 600 !important;
            font-size: 13px !important;
        }

        div[role="listbox"] {
            background-color: #161b22 !important;
        }

        .header-title {
            color: #ffffff;
            font-size: 20px;
            font-weight: 700;
            margin: 0;
            display: inline-block;
        }
        .header-subtitle {
            color: #29b6f6;
            font-size: 13px;
            font-weight: 500;
            margin-left: 8px;
            text-decoration: none;
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


# ==========================================
# 1. DATA ENGINE
# ==========================================
def fetch_raw_1min_candles(instrument_key):
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/1minute"
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
    df_res = (
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
    return df_res


def get_expiry_dates(spot_key):
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={spot_key}"
    try:
        res = HTTP_SESSION.get(url, timeout=5)
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
            df_res["oi_diff"] = df_res["oi"].diff().fillna(df_res["oi"])
            return df_res[["timestamp", "oi_diff"]]
        return pd.DataFrame()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
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


def build_options_apex_dataset(timeframe, num_strikes, selected_expiry):
    df_spot_1m = fetch_raw_1min_candles(SPOT_KEY)
    if df_spot_1m.empty:
        return pd.DataFrame()

    df_spot = resample_candles(df_spot_1m, timeframe)
    chain = fetch_option_chain(SPOT_KEY, selected_expiry)

    if not chain:
        df_spot["pos_builder"] = (df_spot["close"] - df_spot["open"]) * 10
        df_spot["color"] = df_spot["pos_builder"].apply(
            lambda x: "#089981" if x >= 0 else "#f23645"
        )
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

    df_calls = fetch_strike_oi_parallel(call_keys, timeframe)
    df_puts = fetch_strike_oi_parallel(put_keys, timeframe)

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
    merged["pos_builder"] = merged["put_oi_diff"] - merged["call_oi_diff"]
    merged["color"] = merged["pos_builder"].apply(
        lambda x: "#089981" if x >= 0 else "#f23645"
    )

    return merged


# ==========================================
# 2. HEADER & DROPDOWNS BAR
# ==========================================
hdr_col1, hdr_col2, hdr_col3, hdr_col4 = st.columns([4, 1.2, 1.2, 1.6])

with hdr_col1:
    st.markdown(
        """
        <div style="padding-top: 2px;">
            <span class="header-title">Nifty 50</span>
            <span class="header-subtitle">How to use 💡</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with hdr_col2:
    tf_option = st.selectbox(
        "Timeframe",
        options=["3min", "5min"],
        index=0,
        key="tf_select",
        label_visibility="collapsed",
    )

with hdr_col3:
    strike_count = st.selectbox(
        "Strikes",
        options=[3, 5, 10],
        index=0,
        key="strike_select",
        label_visibility="collapsed",
    )

with hdr_col4:
    available_expiries = get_expiry_dates(SPOT_KEY)
    expiry_input = st.selectbox(
        "Expiry",
        options=available_expiries,
        index=0,
        key="expiry_select",
        label_visibility="collapsed",
    )


# ==========================================
# 3. CHART RENDER ENGINE
# ==========================================
@st.fragment(run_every="180s")
def render_live_chart(tf, count, expiry):
    df = build_options_apex_dataset(tf, count, expiry)

    if not df.empty:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.015,
            row_heights=[0.74, 0.46],
        )

        # Main Candlestick Chart (OHLC box hidden; only time displayed)
        fig.add_trace(
            go.Candlestick(
                x=df["timestamp"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="",
                increasing_line_color="#089981",
                decreasing_line_color="#f23645",
                increasing_fillcolor="#089981",
                decreasing_fillcolor="#f23645",
                hovertemplate="%{x|%I:%M %p}<extra></extra>",  # Displays ONLY time
            ),
            row=1,
            col=1,
        )

        # Lower Position Builder Histogram
        fig.add_trace(
            go.Bar(
                x=df["timestamp"],
                y=df["pos_builder"],
                marker_color=df["color"],
                marker_line_width=0,
                name="",
                opacity=0.85,
                hovertemplate="%{x|%I:%M %p}<extra></extra>",  # Displays ONLY time
            ),
            row=2,
            col=1,
        )

        # Layout Config
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0b0e14",
            plot_bgcolor="#0b0e14",
            margin=dict(l=15, r=55, t=10, b=30),
            height=580,
            dragmode="pan",
            xaxis_rangeslider_visible=False,
            hovermode="x",  # Standard unified crosshair cursor hover mode
            showlegend=False,
        )

        # Single Continuous Merged Crosshair Config (Top to Bottom)
        spike_config = dict(
            showspikes=True,
            spikemode="across",
            spikecolor="#787b86",
            spikethickness=1,
            spikedash="dash",
            spikesnap="cursor",
        )

        # Upper X-Axis
        fig.update_xaxes(
            showgrid=True,
            gridcolor="#1e222d",
            gridwidth=1,
            rangebreaks=[dict(bounds=["sat", "mon"])],
            row=1,
            col=1,
            **spike_config,
        )

        # Upper Y-Axis (Price)
        fig.update_yaxes(
            showgrid=True,
            gridcolor="#1e222d",
            gridwidth=1,
            color="#787b86",
            side="right",
            tickfont=dict(family="Arial, sans-serif", size=11),
            row=1,
            col=1,
            **spike_config,
        )

        # Lower X-Axis
        fig.update_xaxes(
            showgrid=True,
            gridcolor="#1e222d",
            color="#787b86",
            tickformat="%I:%M %p",
            type="date",
            rangebreaks=[dict(bounds=["sat", "mon"])],
            row=2,
            col=1,
            **spike_config,
        )

        # Lower Y-Axis Scale
        if len(df) > 2:
            scaled_max = df["pos_builder"].abs().quantile(0.98)
            if scaled_max > 0:
                fig.update_yaxes(
                    range=[-scaled_max * 1.2, scaled_max * 1.2], row=2, col=1
                )

        fig.update_yaxes(
            showgrid=True,
            gridcolor="#1e222d",
            side="right",
            zeroline=True,
            zerolinecolor="#363c4e",
            zerolinewidth=1,
            color="#787b86",
            tickfont=dict(family="Arial, sans-serif", size=10),
            row=2,
            col=1,
            **spike_config,
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
            config={
                "displayModeBar": True,
                "scrollZoom": True,
                "modeBarButtonsToRemove": [
                    "lasso2d",
                    "select2d",
                    "toggleSpikelines",
                ],
                "displaylogo": False,
            },
        )
    else:
        st.error(
            "Unable to fetch market data. Please verify your connection."
        )


render_live_chart(tf_option, strike_count, expiry_input)
