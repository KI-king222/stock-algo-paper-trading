"""Streamlit Algo – Einstandslinie liegt exakt auf dem Kurs zum Kauf."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from data import get_prices, to_eur, native_currency, _fx_to_eur

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PORTFOLIO = STATE / "portfolio_state.json"
LOG = STATE / "decisions_log.json"
STATUS = STATE / "last_run_status.json"
CFG = ROOT / "config.json"

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
st.caption("EUR · Einstand = Kaufkurs – Linie geht durch den Kaufpunkt")

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


def last_buy_dt(trades, ticker) -> Optional[pd.Timestamp]:
    """Zeitpunkt des letzten Kaufs dieser Position."""
    for tr in reversed(trades or []):
        if tr.get("ticker") == ticker and str(tr.get("action")).lower() == "buy":
            try:
                return pd.to_datetime(tr.get("date"), utc=False)
            except Exception:
                return None
    return None


def fetch_live_prices(tickers) -> Dict[str, float]:
    try:
        return get_prices(list(tickers))
    except Exception:
        out = {}
        for t in tickers:
            try:
                h = yf.Ticker(t).history(period="5d")
                if h is None or h.empty:
                    continue
                p = float(h["Close"].dropna().iloc[-1])
                if math.isfinite(p) and p > 0:
                    out[t] = round(to_eur(p, t), 4)
            except Exception:
                pass
        return out


def fetch_history_eur(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        if "Datetime" in df.columns:
            df["Date"] = pd.to_datetime(df["Datetime"], utc=True).dt.tz_convert(None)
        elif "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.tz_convert(None)
        else:
            df["Date"] = pd.to_datetime(df.iloc[:, 0], utc=True).dt.tz_convert(None)

        ccy = native_currency(ticker)
        native = df["Close"].astype(float)
        if ccy == "EUR":
            df["Kurs"] = native
        else:
            rate = _fx_to_eur(ccy)
            df["Kurs"] = native * rate

        df = df.dropna(subset=["Kurs"])
        df = df[df["Kurs"] > 0]
        if df.empty:
            return None
        return df[["Date", "Kurs"]].reset_index(drop=True)
    except Exception:
        return None


def snap_series_to_einstand(
    hist: pd.DataFrame, avg: float, buy_dt: Optional[pd.Timestamp]
) -> pd.DataFrame:
    """
    Skaliert die EUR-Serie so, dass der Kurs zum Kaufzeitpunkt
    exakt dem Einstand entspricht → orangene Linie schneidet die Kurve dort.
    """
    if hist is None or hist.empty or not avg or avg <= 0:
        return hist
    h = hist.copy()

    # Index am Kauf (oder letzter Punkt, wenn Kauf nach letztem Bar / Wochenende)
    if buy_dt is not None and not pd.isna(buy_dt):
        buy_naive = pd.Timestamp(buy_dt).tz_localize(None) if getattr(buy_dt, "tzinfo", None) else pd.Timestamp(buy_dt)
        # nächster Bar <= Kauf, sonst nächster
        deltas = (h["Date"] - buy_naive).abs()
        i = int(deltas.idxmin())
    else:
        i = len(h) - 1

    ref = float(h.loc[i, "Kurs"])
    if ref <= 0:
        return h
    scale = avg / ref
    # nur wenn realistisch (FX/Rundung), nicht bei völlig anderen Daten
    if 0.95 <= scale <= 1.05:
        h["Kurs"] = h["Kurs"] * scale
    return h


def make_chart_ki_style(
    ticker: str,
    avg_cost: float,
    period_label: str,
    buy_dt: Optional[pd.Timestamp] = None,
):
    period, interval = PERIODS[period_label]
    hist = fetch_history_eur(ticker, period, interval)
    if hist is None or hist.empty:
        return None

    avg = round(float(avg_cost), 2) if avg_cost else 0.0
    hist = snap_series_to_einstand(hist, avg, buy_dt)
    hist = hist.reset_index(drop=True)
    hist["i"] = hist.index.astype(int)

    y_min = min(float(hist["Kurs"].min()), avg if avg else float(hist["Kurs"].min()))
    y_max = max(float(hist["Kurs"].max()), avg if avg else float(hist["Kurs"].max()))
    pad = max((y_max - y_min) * 0.03, 0.5)
    n = len(hist)
    n_ticks = min(6, n)
    tick_idx = [int(i * (n - 1) / max(n_ticks - 1, 1)) for i in range(n_ticks)] if n > 1 else [0]
    tick_idx = sorted(set(tick_idx))
    ticktext = [hist.loc[i, "Date"].strftime("%d.%m") for i in tick_idx]
    ccy_note = native_currency(ticker)
    title_extra = f"  ·  EUR" + (f" (aus {ccy_note})" if ccy_note != "EUR" else "")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=hist["i"],
            y=hist["Kurs"],
            mode="lines",
            name=ticker,
            line=dict(width=2.0, color="#30D158"),
            fill="tozeroy",
            fillcolor="rgba(48, 209, 88, 0.13)",
            customdata=hist["Date"],
            hovertemplate="<b>%{y:.2f} €</b><br>%{customdata|%d.%m.%Y %H:%M}<extra></extra>",
        )
    )
    if avg > 0:
        fig.add_hline(
            y=avg,
            line_dash="dot",
            line_color="#FF9F0A",
            line_width=1.4,
            annotation_text=f"Einstand {avg:.2f} €",
            annotation_position="top left",
            annotation_font=dict(size=11, color="#FF9F0A"),
        )
    fig.update_layout(
        title=dict(
            text=f"{ticker}  ·  {period_label}{title_extra}",
            font=dict(size=17, color="#F5F5F7"),
            x=0.0,
            xanchor="left",
        ),
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
            showspikes=True,
            spikemode="across+toaxis",
            spikesnap="cursor",
            spikethickness=1.5,
            spikecolor="#0A84FF",
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.07)",
            side="right",
            tickformat=".2f",
            ticksuffix=" €",
            range=[y_min - pad, y_max + pad],
            fixedrange=False,
            showspikes=True,
        ),
        showlegend=False,
        hoverlabel=dict(
            bgcolor="rgba(28,28,30,0.95)",
            font_size=15,
            font_color="#FFFFFF",
            bordercolor="#30D158",
        ),
    )
    return fig


pf = load_json(PORTFOLIO)
status = load_json(STATUS)
cfg = load_json(CFG) or {}
trades_all = (pf or {}).get("trades") or []
positions = (pf or {}).get("positions") or {}

live_ok = False
if positions:
    live = fetch_live_prices(list(positions.keys()))
    if live:
        live_ok = True
        for t, p in live.items():
            if t in positions:
                positions[t]["current_price"] = p

if live_ok:
    st.caption("Kurse live in EUR · Einstand schneidet die Kurve am Kaufzeitpunkt")
elif positions:
    st.caption("Kurse: gespeicherter Stand")

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
if positions:
    period_label = st.radio(
        "Zeitraum", list(PERIODS.keys()), index=1, horizontal=True,
        label_visibility="collapsed", key="algo_chart_period",
    )
    for t, p in positions.items():
        avg = float(p.get("avg_cost") or 0)
        opened = p.get("opened_at") or p.get("last_action_at")
        buy_dt = None
        try:
            buy_dt = pd.to_datetime(opened) if opened else last_buy_dt(trades_all, t)
        except Exception:
            buy_dt = last_buy_dt(trades_all, t)
        fig = make_chart_ki_style(t, avg, period_label, buy_dt=buy_dt)
        if fig:
            st.plotly_chart(
                fig, use_container_width=True, config=CHART_CONFIG, key=f"algo_chart_{t}_{period_label}"
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

st.divider()
if status:
    st.info(f"**{status.get('last_run', '–')}**  ·  {status.get('headline', '')}")
