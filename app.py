"""Streamlit Algo – Charts wie KI-Version: EUR konsistent, Einstand deckungsgleich."""
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
REPORTS = STATE / "last_reports.json"
MEMORY = STATE / "algo_memory.json"
WATCHLIST = STATE / "watchlist.json"
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
st.caption("Alles in **EUR** · Einstand = Kaufkurs in € · Charts an Live-EUR ausgerichtet")

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


def last_act(trades, ticker) -> str:
    for tr in reversed(trades or []):
        if tr.get("ticker") == ticker:
            return fmt_dt(tr.get("date"))
    return "–"


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


def fetch_history_eur(
    ticker: str, period: str, interval: str, live_eur: Optional[float] = None
) -> Optional[pd.DataFrame]:
    """
    Historie in EUR – gleiche Logik wie KI-data.py, plus:
    - eine FX-Rate für die ganze Serie
    - optional Anker: letzter Punkt = live_eur (wie Tabellen-Kurs)
    so liegt die Einstands-Linie im gleichen Maßstab.
    """
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
            rate = _fx_to_eur(ccy)  # eine Rate für alle Punkte
            df["Kurs"] = native * rate

        df = df.dropna(subset=["Kurs"])
        df = df[df["Kurs"] > 0]
        if df.empty:
            return None

        # An Live-EUR anbinden (Tabelle/Einstand-Raum)
        last = float(df["Kurs"].iloc[-1])
        if live_eur and live_eur > 0 and last > 0:
            scale = float(live_eur) / last
            # nur leichte Korrektur (Rundung/FX-Tick); keine Verzerrung > 2 %
            if 0.98 <= scale <= 1.02:
                df["Kurs"] = df["Kurs"] * scale

        return df[["Date", "Kurs"]].reset_index(drop=True)
    except Exception:
        return None


def make_chart_ki_style(
    ticker: str, avg_cost: float, period_label: str, live_eur: Optional[float] = None
):
    period, interval = PERIODS[period_label]
    hist = fetch_history_eur(ticker, period, interval, live_eur=live_eur)
    if hist is None or hist.empty:
        return None
    hist = hist.copy().reset_index(drop=True)
    hist["i"] = hist.index.astype(int)

    # Einstand exakt wie gebucht (2 Nachkommastellen wie Anzeige)
    avg = round(float(avg_cost), 2) if avg_cost else 0.0

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


def rules_plaintext(cfg: dict) -> str:
    tp = cfg.get("take_profit_pct", "–")
    partial = cfg.get("partial_tp_fraction")
    full = partial is not None and float(partial) >= 0.99
    exit_txt = "Vollverkauf (Lohn)" if full else f"Teilverkauf (~{float(partial)*100:.0f}%)"
    dip = cfg.get("dip_trigger_pct", "–")
    max_dips = cfg.get("max_dip_buys_per_ticker", "–")
    uni = ", ".join(cfg.get("universe") or []) or "offen"
    return f"""### So entscheidet der Algorithmus

**Universum:** {uni}  
**Kurse:** immer **EUR**  
**{exit_txt}** ab ca. +{tp} %  
**Nachkauf** ab ca. {dip} %, max. {max_dips}×  
**Cash-Ziel** ≤ {cfg.get('max_cash_absolute', 700)} €
"""


pf = load_json(PORTFOLIO)
status = load_json(STATUS)
log = load_json(LOG)
reports = load_json(REPORTS)
mem = load_json(MEMORY)
wl = load_json(WATCHLIST)
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
    st.caption("Kurse: **live in EUR** · Charts an denselben EUR-Maßstab gebunden")
elif positions:
    st.caption("Kurse: gespeicherter Stand")

if pf:
    cash = float(pf.get("cash") or 0)
    ext = float(pf.get("external_profit") or 0)
    start = float(pf.get("starting_cash") or 10_000)
    positions_mv = 0.0
    positions_cost = 0.0
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
    r1[0].metric("Gesamtvermögen", f"{gesamtvermoegen:,.2f} €", f"{gesamtvermoegen-start:+.2f} € vs. Start")
    r1[1].metric("Laufendes Depot", f"{laufendes_depot:,.2f} €", f"{laufendes_depot-start:+.2f} € vs. Start")
    r1[2].metric("Offene Aktien", f"{positions_mv:,.2f} €", f"{unrealisiert:+.2f} € P&L")
    r1[3].metric("P&L %", f"{(unrealisiert/positions_cost*100) if positions_cost else 0:+.1f}%")

    r2 = st.columns(3)
    r2[0].metric("Cash", f"{cash:,.2f} €")
    r2[1].metric("Lohn", f"{ext:,.2f} €")
    r2[2].metric("Start", f"{start:,.0f} €")

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
        live_px = float(p.get("current_price") or avg)
        fig = make_chart_ki_style(t, avg, period_label, live_eur=live_px)
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
st.subheader("Letzter Lauf")
if status:
    st.info(f"**{status.get('last_run', '–')}**  ·  {status.get('headline', '')}")

st.subheader("Regeln")
st.markdown(rules_plaintext(cfg))
