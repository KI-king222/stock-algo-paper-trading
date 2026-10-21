"""
Streamlit-Dashboard für stock-algo-paper-trading.
Charts wie KI-Paper-Trading + Regeln in lesbarem Text.
"""
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
CFG = ROOT / "config.json"

st.set_page_config(page_title="Algo Paper Trading", page_icon="📈", layout="wide")
st.title("Algo Paper Trading")
st.caption("Regel-basiert · keine KI · State aus GitHub Actions (ca. 23 Uhr Mo–Fr)")

PERIODS = {
    "1 Tag": ("1d", "5m"),
    "1 Woche": ("5d", "30m"),
    "1 Monat": ("1mo", "1d"),
    "3 Monate": ("3mo", "1d"),
    "6 Monate": ("6mo", "1d"),
    "1 Jahr": ("1y", "1d"),
}


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@st.cache_data(ttl=300)
def fetch_history(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        # Spalte kann Datetime oder Date heißen
        if "Datetime" in df.columns:
            df["ts"] = pd.to_datetime(df["Datetime"])
        elif "Date" in df.columns:
            df["ts"] = pd.to_datetime(df["Date"])
        else:
            df["ts"] = pd.to_datetime(df.iloc[:, 0])
        return df
    except Exception:
        return None


def make_chart(ticker: str, avg_cost: float, period_label: str) -> Optional[go.Figure]:
    period, interval = PERIODS[period_label]
    df = fetch_history(ticker, period, interval)
    if df is None or df.empty:
        return None

    y = df["Close"].astype(float)
    y_min, y_max = float(y.min()), float(y.max())
    pad = (y_max - y_min) * 0.05 if y_max > y_min else y_max * 0.02
    if pad <= 0:
        pad = 1.0

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["ts"],
            y=y,
            mode="lines",
            name=ticker,
            line=dict(width=2, color="#0A84FF"),
            hovertemplate="%{x|%d.%m.%Y %H:%M}<br>%{y:.2f} €<extra></extra>",
        )
    )
    if avg_cost and avg_cost > 0:
        fig.add_hline(
            y=avg_cost,
            line_dash="dot",
            line_color="#FF9F0A",
            annotation_text=f"Ø {avg_cost:.2f} €",
            annotation_position="top left",
        )

    fig.update_layout(
        margin=dict(l=8, r=8, t=28, b=8),
        height=280,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(128,128,128,0.25)",
            rangeslider=dict(visible=False),
            fixedrange=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(128,128,128,0.25)",
            range=[y_min - pad, y_max + pad],
            fixedrange=False,
            tickformat=".2f",
        ),
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        title=dict(text=ticker, font=dict(size=14)),
    )
    # Mobile: Pinch-Zoom erlauben, Box-Select aus
    fig.update_layout(
        modebar=dict(orientation="v"),
    )
    config = {
        "displayModeBar": True,
        "scrollZoom": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
    }
    return fig, config


def rules_plaintext(cfg: dict) -> str:
    mode = cfg.get("mode", "–")
    tp = cfg.get("take_profit_pct", "–")
    partial = cfg.get("partial_tp_fraction")
    partial_pct = f"{float(partial)*100:.0f}%" if partial is not None else "–"
    dip = cfg.get("dip_trigger_pct", "–")
    max_dips = cfg.get("max_dip_buys_per_ticker", "–")
    dip_cash = cfg.get("dip_cash_fraction")
    dip_cash_pct = f"{float(dip_cash)*100:.0f}%" if dip_cash is not None else "–"
    min_dip_sc = cfg.get("min_score_for_dip", "–")
    min_new = cfg.get("min_score_for_new_buy", "–")
    min_hold = cfg.get("min_score_hold_thesis", "–")
    max_pos = cfg.get("max_position_pct_of_equity", "–")
    reinvest = cfg.get("reinvest_idle_cash", False)
    reinv_f = cfg.get("reinvest_cash_fraction")
    reinv_pct = f"{float(reinv_f)*100:.0f}%" if reinv_f is not None else "–"
    max_new = cfg.get("max_new_buys_per_month", "–")
    weekly = cfg.get("weekly_tp_check", False)
    skim = cfg.get("min_monthly_skim_pct", "–")
    skim_f = cfg.get("monthly_skim_fraction")
    skim_pct = f"{float(skim_f)*100:.0f}%" if skim_f is not None else "–"
    mem = cfg.get("memory_enabled", False)
    fee = cfg.get("fee_per_trade", 1)
    start = cfg.get("starting_cash", 10_000)
    uni = cfg.get("universe") or []
    uni_s = ", ".join(uni) if uni else "–"

    return f"""### So entscheidet der Algorithmus

**Modus:** {mode} (kein Chat-KI – nur Zahlen und Regeln)

**Startkapital:** {start:,.0f} € · **Gebühr pro Trade:** {fee} € (Paper)

**Aktien-Universum:** {uni_s}

---

#### Verkaufen (Gewinn mitnehmen)
- Ab **+{tp} %** unrealisiertem Gewinn: **Teilverkauf** von ca. **{partial_pct}** der Position
- Gewinn nach (simulierter) Steuer geht in den **realisierten Lohn** (external_profit), Einstand bleibt investierbar
- **Kein automatischer Verkauf im Minus** – Verluste werden nicht „zum Lohn“ realisiert
- **Monats-Skim:** Ab **+{skim} %** kann zusätzlich ca. **{skim_pct}** der Position mitgenommen werden (Monatsanfang im Daily-Lauf)
- Wöchentlicher TP-Check: {"an" if weekly else "aus"}

#### Nachkaufen (Dip)
- Wenn eine Position **{dip} %** oder tiefer steht und der Score stark genug ist (≥ {min_dip_sc}): Nachkauf möglich
- Maximal **{max_dips}×** Nachkauf pro Ticker
- Dafür max. ca. **{dip_cash_pct}** des verfügbaren Cash, Position nicht größer als ca. **{max_pos} %** des Equity

#### Halten / Score
- Score kommt aus Kennzahlen (Wachstum, Marge, Bilanz, Bewertung), ca. −100 bis +100
- Sehr schwacher Score (unter {min_hold}): eher nur halten, kein aggressives Nachkaufen
- Neukauf nur wenn Score ≥ **{min_new}** und noch Cash da ist (max. **{max_new}** neue Titel pro Monat)

#### Cash nicht parken
- Reinvest freies Cash: {"ja" if reinvest else "nein"} (ca. **{reinv_pct}** des Cash in beste offene Position mit positivem Score)

#### Gedächtnis
- Einfaches Memory: {"an" if mem else "aus"} – merkt erfolgreiche TPs/Dips und justiert Schwellen leicht

---
*Die genauen Zahlen stehen in `config.json` und können sich nach Optimierer-Läufen ändern.*
"""


pf = load_json(PORTFOLIO)
status = load_json(STATUS)
log = load_json(LOG)
cfg = load_json(CFG) or {}

if status:
    st.info(
        f"**Letzter Lauf:** {status.get('last_run', '–')}  ·  "
        f"{status.get('headline', '')}"
    )
else:
    st.warning("Noch kein Lauf – Actions → **Run Algo Daily** starten oder auf 23 Uhr warten.")

c1, c2, c3, c4, c5 = st.columns(5)
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
    c1.metric("Vermögen", f"{wealth:,.2f} €", f"{(wealth/start-1)*100:+.1f}%")
    c2.metric("Equity", f"{equity:,.2f} €")
    c3.metric("Cash", f"{cash:,.2f} €")
    c4.metric("Realisierter Lohn", f"{ext:,.2f} €")
    c5.metric("Steuern (sim.)", f"{float(pf.get('taxes_paid_total') or 0):,.2f} €")
else:
    st.write("Kein Portfolio-State – bitte einmal Algo-Lauf starten.")
    positions = {}

st.divider()

# —— Charts ——
st.subheader("Kurscharts (offene Positionen)")
if positions:
    period_label = st.radio(
        "Zeitraum",
        list(PERIODS.keys()),
        index=2,  # 1 Monat
        horizontal=True,
        key="chart_period",
    )
    for t, p in positions.items():
        avg = float(p.get("avg_cost") or 0)
        px = float(p.get("current_price") or avg)
        sh = float(p.get("shares") or 0)
        pct = ((px - avg) / avg * 100) if avg else 0.0
        st.markdown(f"**{t}** · {sh:g} Stk · Kurs {px:.2f} € · Ø {avg:.2f} € · **{pct:+.1f} %**")
        out = make_chart(t, avg, period_label)
        if out:
            fig, config = out
            st.plotly_chart(fig, use_container_width=True, config=config)
        else:
            st.caption(f"Keine Kursdaten für {t}.")
else:
    st.write("Keine offenen Positionen – keine Charts.")

st.divider()

st.subheader("Offene Positionen")
if positions:
    rows = []
    for t, p in positions.items():
        sh = float(p.get("shares") or 0)
        avg = float(p.get("avg_cost") or 0)
        px = float(p.get("current_price") or avg)
        mv = sh * px
        pnl = (mv - sh * avg) if avg else 0
        pct = (pnl / (sh * avg) * 100) if sh * avg else 0
        rows.append(
            {
                "Ticker": t,
                "Stück": sh,
                "Ø Einstand": round(avg, 2),
                "Kurs": round(px, 2),
                "Wert €": round(mv, 2),
                "P&L €": round(pnl, 2),
                "P&L %": round(pct, 2),
                "Dip-Käufe": p.get("dip_buy_count", 0),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.subheader("Letzte Entscheidungen")
if log and log.get("runs"):
    last = log["runs"][-1]
    st.write(f"**{last.get('source', '')}**")
    dec = last.get("decisions") or []
    if dec:
        st.dataframe(pd.DataFrame(dec), use_container_width=True, hide_index=True)
    scores = last.get("scores") or {}
    if scores:
        st.write("**Scores**")
        nice = {k: round(float(v), 1) for k, v in scores.items()}
        st.json(nice)
else:
    st.write("Noch kein decisions_log.")

st.subheader("Trade-Historie (letzte 30)")
if pf and pf.get("trades"):
    st.dataframe(pd.DataFrame(pf["trades"][-30:]), use_container_width=True, hide_index=True)
else:
    st.write("Keine Trades.")

st.divider()
st.subheader("Regeln (Klartext)")
st.markdown(rules_plaintext(cfg))

with st.expander("Technische Config (JSON)"):
    st.json(cfg)

st.caption(
    "Charts: yfinance · Zoom/Pan am Chart · Daten aus `state/` nach GitHub-Actions-Lauf."
)
