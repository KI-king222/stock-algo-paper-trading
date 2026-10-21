"""
Streamlit-Dashboard für stock-algo-paper-trading (kein KI – reiner Algorithmus).
Deploy: Streamlit Community Cloud → dieses Repo, Main file: app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PORTFOLIO = STATE / "portfolio_state.json"
LOG = STATE / "decisions_log.json"
STATUS = STATE / "last_run_status.json"
CFG = ROOT / "config.json"

st.set_page_config(page_title="Algo Paper Trading", page_icon="📈", layout="wide")
st.title("Algo Paper Trading")
st.caption("Regel-basiert · keine KI · State aus GitHub Actions (ca. 23 Uhr Mo–Fr)")


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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

# Kennzahlen
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
    st.write("Kein `state/portfolio_state.json` – bitte einmal Algo-Lauf starten.")

st.divider()

# Positionen
st.subheader("Offene Positionen")
if pf and pf.get("positions"):
    rows = []
    for t, p in pf["positions"].items():
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
else:
    st.write("Keine offenen Positionen.")

# Letzte Entscheidungen
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
        st.json(scores)
else:
    st.write("Noch kein decisions_log.")

# Trade-Historie
st.subheader("Trade-Historie (letzte 30)")
if pf and pf.get("trades"):
    trades = pf["trades"][-30:]
    st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
else:
    st.write("Keine Trades.")

# Config
with st.expander("Aktuelle Config / Regeln"):
    st.json(cfg)

st.caption(
    "Daten kommen aus dem Repo-Ordner `state/` (GitHub Actions committen nach jedem Lauf). "
    "Streamlit Cloud: Branch main, Main file `app.py`."
)
