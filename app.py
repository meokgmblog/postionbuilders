import concurrent.futures
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

# ==========================================
# PAGE CONFIG & STRICT CSS
# ==========================================
st.set_page_config(
    layout="wide",
    page_title="Nifty 50 | TradingView Multi-Strike Apex",
    page_icon="📈",
)

st.markdown(
    """
    <style>
        html, body, [data-testid="stAppViewContainer"] {
            overflow: hidden !important;
            background-color: #0d1117;
            color: #d1d4dc;
        }
        
        .stApp {
            background-color: #0d1117;
        }
        
        div.block-container {
            padding-top: 0.1rem !important;
            padding-bottom: 0rem !important;
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
            max-width: 100% !important;
        }
        
        div[data-testid="stVerticalBlock"] > div {
            gap: 0.1rem !important;
        }
        
        /* Fixed Horizontal Control Alignment */
        div[data-testid="column"] {
            padding: 0px 2px !important;
        }
        
        /* Dropdown Control Styling */
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
        div[role="listbox"] {
            background-color: #1e222d !important;
        }
        
        /* Compact Metric Cards */
        .metric-card {
            background-color: #131722;
            border: 1px solid #2a2e39;
            border-radius: 4px;
            padding: 2px 8px;
            margin-bottom: 2px;
        }
        .metric-label {
            color: #787b86;
            font-size: 9px;
            font-weight: 600;
            text-transform: uppercase;
            line-height: 1.1;
        }
        .metric-val {
            font-size: 14px;
            font-weight: 700;
            color: #d1d4dc;
            line-height: 1.1;
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
# 2. VISIBLE TOP CONTROLS BAR (DEFAULT: 3min & 3 Strikes)
# ==========================================
top_bar = st.container()
with top_bar:
    c1, c2, c3, c4, c_space = st.columns([2.5, 1.2, 1.2, 2.0, 3.1])

    with c1:
        st.markdown(
            "<h4 style='margin:0; padding-top:2px; font-size:16px; color:#f0f3fa; white-space:nowrap;'>NIFTY 50 <span style='font-size:11px; color:#787b86;'>| MULTI-STRIKE</span></h4>",
            unsafe_allow_html=True,
        )

    with c2:
        tf_option = st.selectbox(
            "TF",
            options=["3min", "5min"],
            index=0,  # Default: 3min
            key="tf_select",
            label_visibility="collapsed",
        )

    with c3:
        strike_count = st.selectbox(
            "Strikes",
            options=[3, 5, 10],
            index=0,  # Default: 3
            key="strike_select",
            label_visibility="collapsed",
        )

    with c4:
        available_expiries = get_expiry_dates(SPOT_KEY)
        expiry_input = st.selectbox(
            "Expiry",
            options=available_expiries,
            index=0,
            key="expiry_select",
            label_visibility="collapsed",
        )


# ==========================================
# 3. LIVE CHART ENGINE
# ==========================================
@st.fragment(run_every="180s")
def render_live_chart(tf, count, expiry):
    df = build_options_apex_dataset(tf, count, expiry)

    if not df.empty:
        last_row = df.iloc[-1]
        prev_close = df.iloc[-2]["close"] if len(df) > 1 else last_row["open"]
        spot_change = last_row["close"] - prev_close
        pct_change = (spot_change / prev_close) * 100
        change_color = "#089981" if spot_change >= 0 else "#f23645"

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.markdown(
                f"""<div class='metric-card'>
                <div class='metric-label'>Spot Price</div>
                <div class='metric-val'>{last_row['close']:.2f} <span style='font-size:10px; color:{change_color};'>({pct_change:+.2f}%)</span></div>
            </div>""",
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown(
                f"""<div class='metric-card'>
                <div class='metric-label'>High / Low</div>
                <div class='metric-val'>{last_row['high']:.2f} / {last_row['low']:.2f}</div>
            </div>""",
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown(
                f"""<div class='metric-card'>
                <div class='metric-label'>Net Position Delta</div>
                <div class='metric-val' style='color:{last_row["color"]};'>{last_row['pos_builder']:,.0f}</div>
            </div>""",
                unsafe_allow_html=True,
            )
        with m4:
            sentiment = (
                "BULLISH" if last_row["pos_builder"] >= 0 else "BEARISH"
            )
            st.markdown(
                f"""<div class='metric-card'>
                <div class='metric-label'>OI Bias</div>
                <div class='metric-val' style='color:{last_row["color"]};'>{sentiment}</div>
            </div>""",
                unsafe_allow_html=True,
            )

        # Plotly Subplots
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.02,
            row_heights=[0.68, 0.52],
        )

        # Candlestick
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

        # Position Builder Histogram
        fig.add_trace(
            go.Bar(
                x=df["timestamp"],
                y=df["pos_builder"],
                marker_color=df["color"],
                marker_line_width=0,
                name="Position Builder",
                opacity=0.9,
            ),
            row=2,
            col=1,
        )

        # Layout Settings
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#131722",
            plot_bgcolor="#131722",
            margin=dict(l=10, r=60, t=10, b=25),
            height=510,
            dragmode="pan",
            xaxis_rangeslider_visible=False,
            hovermode="x unified",
            showlegend=False,
        )

        spike_config = dict(
            showspikes=True,
            spikemode="across+marker",
            spikecolor="#9194a1",
            spikethickness=1,
            spikedash="dash",
            spikesnap="cursor",
        )

        # Top Axis
        fig.update_xaxes(
            showgrid=True,
            gridcolor="#2a2e39",
            gridwidth=1,
            rangebreaks=[dict(bounds=["sat", "mon"])],
            row=1,
            col=1,
            **spike_config,
        )

        fig.update_yaxes(
            showgrid=True,
            gridcolor="#2a2e39",
            gridwidth=1,
            color="#787b86",
            side="right",
            tickfont=dict(family="Courier New, monospace", size=11),
            row=1,
            col=1,
            **spike_config,
        )

        # Bottom Axis
        fig.update_xaxes(
            showgrid=True,
            gridcolor="#2a2e39",
            color="#787b86",
            tickformat="%H:%M",
            type="date",
            rangebreaks=[dict(bounds=["sat", "mon"])],
            row=2,
            col=1,
            **spike_config,
        )

        if len(df) > 2:
            scaled_max = df["pos_builder"].abs().quantile(0.98)
            if scaled_max > 0:
                fig.update_yaxes(
                    range=[-scaled_max * 1.25, scaled_max * 1.25], row=2, col=1
                )

        fig.update_yaxes(
            showgrid=True,
            gridcolor="#2a2e39",
            side="right",
            zeroline=True,
            zerolinecolor="#434651",
            zerolinewidth=1,
            color="#787b86",
            tickfont=dict(family="Courier New, monospace", size=10),
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
            "Unable to fetch market data. Please check Upstox token and connectivity."
        )


render_live_chart(tf_option, strike_count, expiry_input)
