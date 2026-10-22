"""Streamlit-Dashboard: Charts wie KI, Kaufdatum, Aktionsdatum."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PORTFOLIO = STATE / "portfolio_state.json"
LOG = STATE / "decisions_log.json"
STATUS = STATE / "last_run_status.json"
REPORTS = STATE / "last_reports.json"
MEMORY = STATE / "algo_memory.json"
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
st.caption("Kennzahlen + Quartals-Trends + Gehirn · keine Chat-KI · ca. 23 Uhr Mo–Fr")

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
        "zoom2d",
        "select2d",
        "lasso2d",
        "autoScale2d",
        "toggleSpikelines",
        "hoverClosestCartesian",
        "hoverCompareCartesian",
    ],
}

ACTION_MAP = {
    "buy": "kauf",
    "sell": "verkauf",
    "hold": "halten",
    "reinvest": "reinvestition",
    "dip_buy": "nachkauf",
    "new_buy": "neukauf",
    "take_profit": "teilverkauf",
    "sell_tp": "teilverkauf",
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


@st.cache_data(ttl=300)
def fetch_history(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
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
        df["Kurs"] = df["Close"].astype(float)
        return df[["Date", "Kurs"]].dropna()
    except Exception:
        return None


def make_chart_ki_style(ticker: str, avg_cost: float, period_label: str):
    period, interval = PERIODS[period_label]
    hist = fetch_history(ticker, period, interval)
    if hist is None or hist.empty:
        return None
    hist = hist.copy().reset_index(drop=True)
    hist["i"] = hist.index.astype(int)
    y_min = min(float(hist["Kurs"].min()), avg_cost if avg_cost else float(hist["Kurs"].min()))
    y_max = max(float(hist["Kurs"].max()), avg_cost if avg_cost else float(hist["Kurs"].max()))
    pad = max((y_max - y_min) * 0.03, 0.5)
    n = len(hist)
    n_ticks = min(6, n)
    tick_idx = (
        [int(i * (n - 1) / max(n_ticks - 1, 1)) for i in range(n_ticks)] if n > 1 else [0]
    )
    tick_idx = sorted(set(tick_idx))
    ticktext = [hist.loc[i, "Date"].strftime("%d.%m") for i in tick_idx]
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
    if avg_cost and avg_cost > 0:
        fig.add_hline(
            y=avg_cost,
            line_dash="dot",
            line_color="#FF9F0A",
            line_width=1.4,
            annotation_text=f"Einstand {avg_cost:.2f} €",
            annotation_position="top left",
            annotation_font=dict(size=11, color="#FF9F0A"),
        )
    fig.update_layout(
        title=dict(
            text=f"{ticker}  ·  {period_label}",
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
    partial_pct = f"{float(partial)*100:.0f}%" if partial is not None else "–"
    dip = cfg.get("dip_trigger_pct", "–")
    max_dips = cfg.get("max_dip_buys_per_ticker", "–")
    skim = cfg.get("min_monthly_skim_pct", "–")
    uni = ", ".join(cfg.get("universe") or []) or "–"
    return f"""### So entscheidet der Algorithmus

1. Finanzzahlen auswerten  
2. Kursverlauf + Score  
3. Gehirn  
4. Regeln (Teilverkauf, Nachkauf, Halten – kein Verkauf im Minus)

**Universum:** {uni}  
**Teilverkauf** ab ca. +{tp} % (~{partial_pct})  
**Nachkauf** ab ca. {dip} %, max. {max_dips}×  
**Monats-Mitnahme** ab ca. +{skim} %
"""


pf = load_json(PORTFOLIO)
status = load_json(STATUS)
log = load_json(LOG)
reports = load_json(REPORTS)
mem = load_json(MEMORY)
cfg = load_json(CFG) or {}
trades_all = (pf or {}).get("trades") or []

if status:
    st.info(f"**Letzter Lauf:** {status.get('last_run', '–')}  ·  {status.get('headline', '')}")
else:
    st.warning("Noch kein Lauf – Actions → Run Algo Daily.")

c1, c2, c3, c4, c5 = st.columns(5)
positions = {}
if pf:
    cash = float(pf.get("cash") or 0)
    ext = float(pf.get("external_profit") or 0)
    positions = pf.get("positions") or {}
    equity = cash + sum(
        float(p.get("shares", 0)) * float(p.get("current_price") or p.get("avg_cost") or 0)
        for p in positions.values()
    )
    wealth = equity + ext
    start = float(pf.get("starting_cash") or 10_000)
    c1.metric("Gesamtvermögen", f"{wealth:,.2f} €", f"{(wealth/start-1)*100:+.1f}%")
    c2.metric("Depot + Cash", f"{equity:,.2f} €")
    c3.metric("Rest-Cash", f"{cash:,.2f} €")
    c4.metric("Realisierter Lohn", f"{ext:,.2f} €")
    c5.metric("Steuern (sim.)", f"{float(pf.get('taxes_paid_total') or 0):,.2f} €")

st.divider()
st.subheader("Finanzbericht-Analyse (letzter Lauf)")
if reports and reports.get("reports"):
    for t, r in reports["reports"].items():
        with st.expander(f"{t} · Score {r.get('score', '–')}"):
            for line in r.get("findings") or ["–"]:
                st.write(f"· {line}")
elif log and log.get("runs"):
    last = log["runs"][-1]
    for t, txt in (last.get("report_summaries") or {}).items():
        st.write(f"**{t}:** {txt}")

st.divider()
st.subheader("Kursverlauf der aktuellen Positionen")
if positions:
    period_label = st.radio(
        "Zeitraum", list(PERIODS.keys()), index=1, horizontal=True,
        label_visibility="collapsed", key="algo_chart_period",
    )
    for t, p in positions.items():
        avg = float(p.get("avg_cost") or 0)
        fig = make_chart_ki_style(t, avg, period_label)
        if fig:
            st.plotly_chart(fig, use_container_width=True, config=CHART_CONFIG, key=f"algo_chart_{t}_{period_label}")
else:
    st.write("Keine Positionen.")

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
        opened = fmt_dt(p.get("opened_at")) if p.get("opened_at") else first_buy(trades_all, t)
        last_a = fmt_dt(p.get("last_action_at")) if p.get("last_action_at") else last_act(trades_all, t)
        rows.append(
            {
                "Ticker": t,
                "Kaufdatum": opened,
                "Letzte Aktion": last_a,
                "Stück": sh,
                "Ø Einstand": round(avg, 2),
                "Kurs": round(px, 2),
                "Wert €": round(mv, 2),
                "P&L %": round(pct, 2),
                "Nachkäufe": p.get("dip_buy_count", 0),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.subheader("Letzte Entscheidungen")
if log and log.get("runs"):
    last = log["runs"][-1]
    run_date = fmt_dt(last.get("date"))
    st.write(f"**{last.get('source', '')}** · **Datum Lauf:** {run_date}")
    dec = last.get("decisions") or []
    if dec:
        fixed = []
        for e in dec:
            row = dict(e)
            if "aktion" not in row and "action" in row:
                row["aktion"] = ACTION_MAP.get(str(row["action"]), row["action"])
            fixed.append(
                {
                    "Datum": run_date,
                    "Ticker": row.get("ticker"),
                    "Aktion": row.get("aktion") or row.get("action"),
                    "Stück": row.get("stück") or row.get("shares") or "–",
                    "Score": row.get("score"),
                    "Grund": row.get("grund") or row.get("reason"),
                }
            )
        st.dataframe(pd.DataFrame(fixed), use_container_width=True, hide_index=True)

st.subheader("Trade-Historie (letzte 30)")
if trades_all:
    trades = [
        {
            "Datum": fmt_dt(t.get("date")),
            "Ticker": t.get("ticker"),
            "Aktion": ACTION_MAP.get(str(t.get("action")), t.get("action")),
            "Stück": t.get("shares"),
            "Kurs": t.get("price"),
            "Grund": t.get("reason"),
            "Mitgenommener Gewinn": t.get("locked_profit"),
        }
        for t in trades_all[-30:]
    ]
    st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Regeln (Klartext)")
st.markdown(rules_plaintext(cfg))
with st.expander("Technische Config (JSON)"):
    st.json(cfg)
