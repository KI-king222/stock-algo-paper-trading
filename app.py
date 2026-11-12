"""Streamlit Algo – Einstand trifft Kurve am Kaufzeitpunkt."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data import get_prices, native_currency, _fx_to_eur

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PORTFOLIO = STATE / "portfolio_state.json"
STATUS = STATE / "last_run_status.json"

st.set_page_config(page_title="Algo Paper Trading", page_icon="📈", layout="wide")
st.markdown(
    """
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    div[data-testid="stPlotlyChart"] { margin-bottom: 0.8rem; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("Algo Paper Trading")
st.caption("EUR · Chart v5 · Einstand trifft Kurve am Kauftag")

PERIODS = {
    "1T": ("1d", "5m"),
    "1W": ("5d", "15m"),
    "1M": ("1mo", "1h"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "1J": ("1y", "1d"),
}
CHART_CONFIG = {
    "displayModeBar": True,
    "displaylogo": False,
    "scrollZoom": True,
    "responsive": True,
    "doubleClick": "reset",
    "modeBarButtonsToRemove": [
        "zoom2d", "select2d", "lasso2d", "autoScale2d",
        "toggleSpikelines", "hoverClosestCartesian", "hoverCompareCartesian",
    ],
}
ACTION_MAP = {
    "buy": "kauf", "sell": "verkauf", "hold": "halten",
    "reinvest": "reinvestition", "dip_buy": "nachkauf", "new_buy": "neukauf",
    "take_profit": "verkauf", "sell_tp": "verkauf",
}


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def fmt_dt(s) -> str:
    if not s:
        return "–"
    return str(s).replace("T", " ")[:19]


def first_buy(trades, ticker) -> str:
    for tr in trades or []:
        if tr.get("ticker") == ticker and str(tr.get("action")).lower() == "buy":
            return fmt_dt(tr.get("date"))
    return "–"


def first_buy_dt(trades, ticker) -> Optional[pd.Timestamp]:
    for tr in trades or []:
        if tr.get("ticker") == ticker and str(tr.get("action")).lower() == "buy":
            try:
                return pd.to_datetime(tr.get("date"), errors="coerce")
            except Exception:
                return None
    return None


def to_berlin(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series, utc=True, errors="coerce")
    try:
        return s.dt.tz_convert("Europe/Berlin").dt.tz_localize(None)
    except Exception:
        return pd.to_datetime(series, errors="coerce")


def fetch_live_prices(tickers) -> Dict[str, float]:
    try:
        return get_prices(list(tickers))
    except Exception:
        return {}


def fetch_history_eur(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    import yfinance as yf

    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        if "Datetime" in df.columns:
            raw_dt = df["Datetime"]
        elif "Date" in df.columns:
            raw_dt = df["Date"]
        else:
            raw_dt = df.iloc[:, 0]
        df["Date"] = to_berlin(raw_dt)
        ccy = native_currency(ticker)
        native = df["Close"].astype(float)
        if ccy == "EUR":
            df["Kurs"] = native
        else:
            df["Kurs"] = native * float(_fx_to_eur(ccy))
        df = df.dropna(subset=["Kurs", "Date"])
        df = df[df["Kurs"] > 0]
        if df.empty:
            return None
        return df[["Date", "Kurs"]].reset_index(drop=True)
    except Exception:
        return None


def align_to_einstand(hist: pd.DataFrame, avg: float, buy_dt: Optional[pd.Timestamp]) -> pd.DataFrame:
    if hist is None or hist.empty or not avg or avg <= 0 or buy_dt is None or pd.isna(buy_dt):
        return hist
    buy_naive = pd.to_datetime(buy_dt)
    if getattr(buy_naive, "tzinfo", None) is not None:
        buy_naive = buy_naive.tz_localize(None)
    t_min, t_max = hist["Date"].min(), hist["Date"].max()
    if buy_naive < t_min - pd.Timedelta(hours=12) or buy_naive > t_max + pd.Timedelta(hours=12):
        return hist
    diffs = (hist["Date"] - buy_naive).abs()
    i = int(diffs.idxmin())
    px = float(hist.loc[i, "Kurs"])
    if not math.isfinite(px) or px <= 0:
        return hist
    if abs(px - avg) / avg < 0.0015:
        hist = hist.copy()
        hist.attrs["aligned"] = True
        hist.attrs["align_idx"] = i
        return hist
    factor = avg / px
    if factor < 0.85 or factor > 1.15:
        return hist
    hist = hist.copy()
    hist["Kurs"] = hist["Kurs"] * factor
    hist.attrs["aligned"] = True
    hist.attrs["align_idx"] = i
    return hist


def make_chart_ki_style(
    ticker: str, avg_cost: float, period_label: str, buy_dt: Optional[pd.Timestamp] = None
):
    period, interval = PERIODS[period_label]
    hist = fetch_history_eur(ticker, period, interval)
    if hist is None or hist.empty:
        return None

    avg = round(float(avg_cost), 2) if avg_cost else 0.0
    hist = align_to_einstand(hist, avg, buy_dt)
    hist = hist.reset_index(drop=True)
    hist["i"] = hist.index.astype(int)

    last_dt = hist["Date"].iloc[-1]
    stand = last_dt.strftime("%d.%m. %H:%M") if pd.notna(last_dt) else "?"
    last_px = float(hist["Kurs"].iloc[-1])
    lo = float(hist["Kurs"].min())
    hi = float(hist["Kurs"].max())
    aligned = bool(getattr(hist, "attrs", {}).get("aligned"))
    align_idx = getattr(hist, "attrs", {}).get("align_idx")

    y_min = min(lo, avg if avg else lo)
    y_max = max(hi, avg if avg else hi)
    mid = (y_min + y_max) / 2
    natural = y_max - y_min
    ref = avg if avg else mid
    min_span = max(ref * 0.10, natural, 1.0)
    if natural < min_span:
        half = min_span / 2
        y_min, y_max = mid - half, mid + half
    pad = max((y_max - y_min) * 0.04, 0.3)

    n = len(hist)
    n_ticks = min(6, n)
    tick_idx = [int(i * (n - 1) / max(n_ticks - 1, 1)) for i in range(n_ticks)] if n > 1 else [0]
    tick_idx = sorted(set(tick_idx))
    if period_label == "1T":
        ticktext = [hist.loc[i, "Date"].strftime("%H:%M") for i in tick_idx]
    else:
        ticktext = [hist.loc[i, "Date"].strftime("%d.%m %H:%M") for i in tick_idx]
    ccy_note = native_currency(ticker)
    title = f"{ticker} · {period_label} · EUR · {stand}" + (
        f" · aus {ccy_note}" if ccy_note != "EUR" else ""
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=hist["i"],
            y=hist["Kurs"],
            mode="lines",
            name=ticker,
            line=dict(width=2.0, color="#30D158"),
            customdata=hist["Date"],
            hovertemplate="<b>%{y:.2f} €</b><br>%{customdata|%d.%m.%Y %H:%M}<extra></extra>",
        )
    )
    if avg > 0:
        fig.add_hline(
            y=avg,
            line_dash="dot",
            line_color="#FF9F0A",
            line_width=1.6,
            annotation_text=f"Einstand {avg:.2f} €",
            annotation_position="top left",
            annotation_font=dict(size=11, color="#FF9F0A"),
        )
    if aligned and align_idx is not None and 0 <= int(align_idx) < len(hist):
        fig.add_trace(
            go.Scatter(
                x=[int(align_idx)],
                y=[avg],
                mode="markers",
                marker=dict(size=10, color="#FF9F0A", symbol="diamond"),
                hovertemplate=f"Kauf @ {avg:.2f} €<extra></extra>",
            )
        )
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#F5F5F7"), x=0.0, xanchor="left"),
        height=380,
        margin=dict(l=8, r=8, t=50, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#AEAEB2", size=11),
        hovermode="x unified",
        dragmode="pan",
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.07)",
            tickmode="array",
            tickvals=tick_idx,
            ticktext=ticktext,
            rangeslider=dict(visible=False),
            fixedrange=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.07)",
            side="right",
            tickformat=".2f",
            ticksuffix=" €",
            range=[y_min - pad, y_max + pad],
            fixedrange=False,
            autorange=False,
        ),
        showlegend=False,
    )
    return fig, stand, last_px, lo, hi, aligned


pf = load_json(PORTFOLIO)
status = load_json(STATUS)
trades_all = (pf or {}).get("trades") or []
positions = (pf or {}).get("positions") or {}

live = {}
if positions:
    live = fetch_live_prices(list(positions.keys())) or {}
    for t, p in live.items():
        if t in positions:
            positions[t]["current_price"] = p

if live:
    st.success("Live-Kurse geladen")
else:
    st.warning("Live-Kurse nicht geladen – gespeicherter Stand")

if pf:
    cash = float(pf.get("cash") or 0)
    ext = float(pf.get("external_profit") or 0)
    start = float(pf.get("starting_cash") or 10_000)
    positions_mv = positions_cost = 0.0
    for p in positions.values():
        sh = float(p.get("shares", 0))
        avg = float(p.get("avg_cost") or 0)
        px = float(p.get("current_price") or avg)
        positions_mv += sh * px
        positions_cost += sh * avg
    laufendes_depot = cash + positions_mv
    gesamtvermoegen = laufendes_depot + ext
    unrealisiert = positions_mv - positions_cost

    r1 = st.columns(4)
    r1[0].metric("Gesamtvermögen", f"{gesamtvermoegen:,.2f} €", f"{gesamtvermoegen-start:+.2f} €")
    r1[1].metric("Laufendes Depot", f"{laufendes_depot:,.2f} €")
    r1[2].metric("Offene Aktien", f"{positions_mv:,.2f} €", f"{unrealisiert:+.2f} €")
    r1[3].metric("Cash / Lohn", f"{cash:,.0f} € / {ext:,.0f} €")

st.divider()
st.subheader("Offene Positionen")
if positions:
    rows = []
    for t, p in positions.items():
        sh = float(p.get("shares") or 0)
        avg = float(p.get("avg_cost") or 0)
        px = float(p.get("current_price") or avg)
        mv = sh * px
        pnl = mv - sh * avg
        pct = (pnl / (sh * avg) * 100) if sh * avg else 0
        rows.append({
            "Ticker": t,
            "Kaufdatum": fmt_dt(p.get("opened_at")) if p.get("opened_at") else first_buy(trades_all, t),
            "Stück": sh,
            "Ø Einstand €": round(avg, 2),
            "Kurs €": round(px, 2),
            "Wert €": round(mv, 2),
            "P&L €": round(pnl, 2),
            "P&L %": round(pct, 2),
            "Notiz": "US→EUR" if native_currency(t) != "EUR" else "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Kursverlauf (EUR)")
st.caption("Chart v5 · Einstand trifft die Kurve am Kauftag (orangener Diamant)")
if positions:
    period_label = st.radio(
        "Zeitraum",
        list(PERIODS.keys()),
        index=1,
        horizontal=True,
        label_visibility="collapsed",
        key="algo_chart_period_v5",
    )
    for t, p in positions.items():
        avg = float(p.get("avg_cost") or 0)
        bdt = first_buy_dt(trades_all, t)
        if bdt is None and p.get("opened_at"):
            bdt = pd.to_datetime(p.get("opened_at"), errors="coerce")
        out = make_chart_ki_style(t, avg, period_label, buy_dt=bdt)
        if out:
            fig, stand, last_px, lo, hi, aligned = out
            extra = " · ✓ am Graph" if aligned else ""
            st.caption(
                f"**{t}** Stand **{stand}** · Chart **{last_px:.2f} €** · "
                f"Einstand **{avg:.2f} €**{extra}"
            )
            st.plotly_chart(
                fig, use_container_width=True, config=CHART_CONFIG, key=f"algo_v5_{t}_{period_label}"
            )

st.divider()
st.subheader("Trade-Historie")
if trades_all:
    st.dataframe(pd.DataFrame([{
        "Datum": fmt_dt(t.get("date")),
        "Ticker": t.get("ticker"),
        "Aktion": ACTION_MAP.get(str(t.get("action")), t.get("action")),
        "Stück": t.get("shares"),
        "Kurs €": t.get("price"),
        "Grund": t.get("reason"),
    } for t in trades_all[-40:]]), use_container_width=True, hide_index=True)

if status:
    st.info(f"**{status.get('last_run', '–')}**  ·  {status.get('headline', '')}")
