"""Streamlit-Dashboard: Charts, Scores erklärt, DE-Begriffe."""
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
st.caption("Regel-basiert · keine KI · ca. 23 Uhr Mo–Fr")

PERIODS = {
    "1 Tag": ("1d", "5m"),
    "1 Woche": ("5d", "30m"),
    "1 Monat": ("1mo", "1d"),
    "3 Monate": ("3mo", "1d"),
    "6 Monate": ("6mo", "1d"),
    "1 Jahr": ("1y", "1d"),
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


@st.cache_data(ttl=300)
def fetch_history(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        if "Datetime" in df.columns:
            df["ts"] = pd.to_datetime(df["Datetime"])
        elif "Date" in df.columns:
            df["ts"] = pd.to_datetime(df["Date"])
        else:
            df["ts"] = pd.to_datetime(df.iloc[:, 0])
        return df
    except Exception:
        return None


def make_chart(ticker: str, avg_cost: float, period_label: str):
    period, interval = PERIODS[period_label]
    df = fetch_history(ticker, period, interval)
    if df is None or df.empty:
        return None
    y = df["Close"].astype(float)
    y_min, y_max = float(y.min()), float(y.max())
    pad = (y_max - y_min) * 0.05 if y_max > y_min else max(y_max * 0.02, 1.0)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["ts"],
            y=y,
            mode="lines",
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
        xaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.25)", rangeslider=dict(visible=False)),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(128,128,128,0.25)",
            range=[y_min - pad, y_max + pad],
            tickformat=".2f",
        ),
        dragmode="pan",
        hovermode="x unified",
        showlegend=False,
        title=dict(text=ticker, font=dict(size=14)),
    )
    config = {
        "displayModeBar": True,
        "scrollZoom": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
    }
    return fig, config


def rules_plaintext(cfg: dict) -> str:
    tp = cfg.get("take_profit_pct", "–")
    partial = cfg.get("partial_tp_fraction")
    partial_pct = f"{float(partial)*100:.0f}%" if partial is not None else "–"
    dip = cfg.get("dip_trigger_pct", "–")
    max_dips = cfg.get("max_dip_buys_per_ticker", "–")
    skim = cfg.get("min_monthly_skim_pct", "–")
    uni = ", ".join(cfg.get("universe") or []) or "–"
    return f"""### So entscheidet der Algorithmus

**Kein Chat-KI** – nur Kennzahlen-Score und feste Regeln.

**Universum:** {uni}

**Teilverkauf** ab ca. **+{tp} %** (etwa {partial_pct} der Position) → Gewinn landet im **realisierten Lohn**.

**Nachkauf** möglich ab ca. **{dip} %**, max. **{max_dips}×** pro Aktie.

**Monats-Mitnahme** ab ca. **+{skim} %** (Monatsanfang).

**Kein Verkauf im Minus.** Rest-Cash ist **kein** Gewinn.
"""


pf = load_json(PORTFOLIO)
status = load_json(STATUS)
log = load_json(LOG)
cfg = load_json(CFG) or {}

if status:
    st.info(f"**Letzter Lauf:** {status.get('last_run', '–')}  ·  {status.get('headline', '')}")
    if status.get("hinweis"):
        st.caption(status["hinweis"])
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
    c2.metric("Depot + Cash (Equity)", f"{equity:,.2f} €")
    c3.metric("Rest-Cash (nicht investiert)", f"{cash:,.2f} €")
    c4.metric("Realisierter Lohn (nur nach Verkauf)", f"{ext:,.2f} €")
    c5.metric("Steuern (Simulation)", f"{float(pf.get('taxes_paid_total') or 0):,.2f} €")
    st.caption(
        "**Rest-Cash** = übrig gebliebenes Geld nach Käufen – **kein Gewinn**. "
        "**Realisierter Lohn** steigt erst, wenn der Algo **im Plus verkauft**."
    )
else:
    st.write("Kein Portfolio-State.")

st.divider()
st.subheader("Kurscharts (offene Positionen)")
if positions:
    period_label = st.radio("Zeitraum", list(PERIODS.keys()), index=2, horizontal=True)
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
    st.write("Keine offenen Positionen.")

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
        rows.append(
            {
                "Ticker": t,
                "Stück": sh,
                "Ø Einstand": round(avg, 2),
                "Kurs": round(px, 2),
                "Wert €": round(mv, 2),
                "P&L €": round(pnl, 2),
                "P&L %": round(pct, 2),
                "Nachkäufe": p.get("dip_buy_count", 0),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.subheader("Was sind die Scores?")
st.markdown(
    """
Der **Score** ist eine Note von ca. **−100 bis +100** für jede Aktie.  
Der Algorithmus rechnet sie aus **Geschäfts-Kennzahlen** (kein Bauchgefühl):

| Baustein | Bedeutung (kurz) |
|----------|------------------|
| **Wachstum** | Umsatz- / Gewinnwachstum |
| **Rentabilität** | Margen, Eigenkapitalrendite |
| **Bilanz** | Verschuldung, Liquidität |
| **Bewertung** | z. B. KGV (teuer/günstig) |

**Hoch (z. B. 40–60)** = Kennzahlen wirken solide → eher halten / nachkaufen / reinvestieren.  
**Niedrig / negativ** = schwächer → vorsichtiger, kein aggressiver Nachkauf.  

Der Score ist **kein Kurs-Prognose** und **kein garantierter Gewinn** – nur ein Filter für die Regeln.
"""
)

st.subheader("Letzte Entscheidungen")
if log and log.get("runs"):
    last = log["runs"][-1]
    st.write(f"**{last.get('source', '')}**")
    dec = last.get("decisions") or []
    if dec:
        # Alte EN-Keys auf DE mappen
        fixed = []
        for e in dec:
            row = dict(e)
            if "aktion" not in row and "action" in row:
                row["aktion"] = ACTION_MAP.get(str(row["action"]), row["action"])
            if "grund" not in row and "reason" in row:
                row["grund"] = row["reason"]
            if "stück" not in row and "shares" in row:
                row["stück"] = row["shares"]
            # Anzeige-Spalten
            fixed.append(
                {
                    "Ticker": row.get("ticker"),
                    "Aktion": row.get("aktion") or row.get("action"),
                    "Stück": row.get("stück") or row.get("shares") or "–",
                    "Score": row.get("score"),
                    "Grund": row.get("grund") or row.get("reason"),
                }
            )
        st.dataframe(pd.DataFrame(fixed), use_container_width=True, hide_index=True)
    scores = last.get("scores") or {}
    if scores:
        st.write("**Scores (gerundet)**")
        st.json({k: round(float(v), 1) for k, v in scores.items()})
else:
    st.write("Noch kein Log.")

st.subheader("Trade-Historie (letzte 30)")
if pf and pf.get("trades"):
    trades = []
    for t in pf["trades"][-30:]:
        trades.append(
            {
                "Datum": t.get("date", "")[:19],
                "Ticker": t.get("ticker"),
                "Aktion": ACTION_MAP.get(str(t.get("action")), t.get("action")),
                "Stück": t.get("shares"),
                "Kurs": t.get("price"),
                "Gebühr": t.get("fee"),
                "Grund": t.get("reason"),
                "Mitgenommener Gewinn": t.get("locked_profit"),
            }
        )
    st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
else:
    st.write("Keine Trades.")

st.divider()
st.subheader("Regeln (Klartext)")
st.markdown(rules_plaintext(cfg))
with st.expander("Technische Config (JSON)"):
    st.json(cfg)
