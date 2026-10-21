#!/usr/bin/env python3
"""
v4.1 auf allen bisherigen Fenstern + Kursverlauf 5/10/20 Tage.
Speichert pro Fenster und eine Gesamt-Summary.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import Portfolio  # noqa: E402
from data import get_financial_report, get_fundamentals  # noqa: E402
from algo.scoring import score_combined  # noqa: E402
from algo.decision_engine import RunContext, apply_decisions, decide  # noqa: E402
from algo.memory import AlgoMemory  # noqa: E402

WINDOWS = [
    {"tag": "2025-03", "start": "2025-03-03", "end": "2025-03-31"},
    {"tag": "2025-10", "start": "2025-10-06", "end": "2025-11-03"},
    {"tag": "2026-10-2w", "start": "2026-09-30", "end": "2026-10-21"},
]

CFG = {
    "mode": "active_income_v4",
    "starting_cash": 10000.0,
    "fee_per_trade": 1.0,
    "take_profit_pct": 4.5,
    "partial_tp_fraction": 0.35,
    "trail_giveback_pct": 2.0,
    "dip_trigger_pct": -12.0,
    "max_dip_buys_per_ticker": 1,
    "max_position_pct_of_equity": 35.0,
    "dip_cash_fraction": 0.25,
    "min_score_for_dip": 5,
    "min_score_for_new_buy": 30,
    "min_score_for_reinvest": 35,
    "min_score_hold_thesis": -30,
    "no_add_if_pnl_below_pct": -3.0,
    "reinvest_idle_cash": True,
    "reinvest_cash_fraction": 0.30,
    "max_new_buys_per_month": 1,
    "min_monthly_skim_pct": 4.0,
    "monthly_skim_fraction": 0.30,
    "memory_enabled": True,
    "regime_block_buys_if_median_mom5_below": -2.0,
    "universe": ["SAP.DE", "HNR1.DE", "MUV2.DE", "ENR.DE"],
    "use_momentum": True,
    "cooldown_days": 5,
}


def fetch_close(tickers, start, end):
    import yfinance as yf

    raw = yf.download(
        tickers, start=start, end=end, auto_adjust=True, progress=False, threads=True
    )
    if raw.empty:
        raise RuntimeError("Keine Kursdaten")
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"].copy()
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0]})
    close = close.dropna(how="all")
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close.reindex(columns=[t for t in tickers if t in close.columns])


def prices_on(close, day):
    row = close.loc[day]
    out = {}
    for t, v in row.items():
        try:
            f = float(v)
            if math.isfinite(f) and f > 0:
                out[str(t)] = f
        except (TypeError, ValueError):
            pass
    return out


def momentum_at(close, ticker, day):
    if ticker not in close.columns:
        return {"m5": None, "m10": None, "m20": None}
    s = close[ticker].loc[:day].dropna()
    if len(s) < 3:
        return {"m5": None, "m10": None, "m20": None}
    last = float(s.iloc[-1])

    def ret(n):
        if len(s) < n:
            return None
        return (last / float(s.iloc[-n]) - 1) * 100

    return {"m5": ret(5), "m10": ret(10), "m20": ret(20)}


def run_window(tag, period_start, period_end, close_full, base_fund, base_report):
    close = close_full.loc[period_start:period_end]
    days = list(close.index)
    if len(days) < 5:
        return None

    cfg = dict(CFG)
    pf = Portfolio(starting_cash=cfg["starting_cash"], fee_per_trade=cfg["fee_per_trade"])
    mem = AlgoMemory()
    ctx = RunContext(cooldown_days=5)

    # Score-gewichteter Start
    p0 = prices_on(close, days[0])
    weights = {}
    for t in p0:
        mom = momentum_at(close_full, t, days[0])
        sc, _, _ = score_combined(base_fund.get(t) or {}, base_report.get(t), mom)
        weights[t] = max(sc, 5.0)
    tw = sum(weights.values()) or 1.0
    for t, p in p0.items():
        budget = pf.cash * (weights[t] / tw) * 0.95
        n = int((budget - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason=f"Start Score {weights[t]:.0f}")
            ctx.update_peak(t, p)

    daily = []
    last_week = days[0].isocalendar()[1]

    for day in days[1:]:
        prices = prices_on(close, day)
        if not prices:
            continue
        scores, details = {}, {}
        for t in set(list(prices) + list(pf.positions)):
            mom = momentum_at(close_full, t, day)
            sc, parts, _ = score_combined(
                base_fund.get(t) or {}, base_report.get(t), mom
            )
            scores[t] = sc
            details[t] = parts

        iso_w = day.isocalendar()[1]
        full = iso_w != last_week or day == days[-1]
        decisions = decide(
            pf, prices, scores, details, cfg, day.to_pydatetime(), mem, False, ctx
        )
        if not full:
            decisions = [d for d in decisions if d.action == "take_profit"]
        else:
            last_week = iso_w

        entries = apply_decisions(pf, decisions, prices, day.to_pydatetime(), mem, ctx)
        pf.update_prices(prices)
        daily.append(
            {
                "date": str(day.date()),
                "wealth": round(pf.total_wealth(), 2),
                "cash": round(pf.cash, 2),
                "external_profit": round(pf.external_profit, 2),
                "actions": [
                    {
                        "ticker": e.get("ticker"),
                        "aktion": e.get("aktion"),
                        "stück": e.get("stück"),
                        "grund": e.get("grund"),
                    }
                    for e in entries
                    if e.get("aktion") not in ("halten",) or e.get("stück")
                ],
            }
        )

    return {
        "version": "v4.1",
        "tag": tag,
        "period": {
            "start": str(days[0].date()),
            "end": str(days[-1].date()),
            "trading_days": len(days),
        },
        "end": {
            "wealth": round(pf.total_wealth(), 2),
            "equity": round(pf.total_equity(), 2),
            "cash": round(pf.cash, 2),
            "external_profit": round(pf.external_profit, 2),
            "return_pct": round((pf.total_wealth() / pf.starting_cash - 1) * 100, 2),
            "trades": len(pf.trades),
            "positions": {k: v.to_dict() for k, v in pf.positions.items()},
        },
        "daily": daily,
        "trades": [t.to_dict() for t in pf.trades],
    }


def main():
    tickers = CFG["universe"]
    # gemeinsamer Datenbereich
    starts = [w["start"] for w in WINDOWS]
    ends = [w["end"] for w in WINDOWS]
    buf = (datetime.fromisoformat(min(starts)) - timedelta(days=35)).strftime("%Y-%m-%d")
    close_full = fetch_close(tickers, buf, max(ends))

    base_fund, base_report = {}, {}
    print("=== Analyse Basis ===")
    for t in tickers:
        base_fund[t] = get_fundamentals(t)
        base_report[t] = get_financial_report(t)
        sc, _, txt = score_combined(base_fund[t], base_report[t])
        print(f"  {t}: {sc:+.1f}")

    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    summary_rows = []

    for w in WINDOWS:
        print(f"\n=== Fenster {w['tag']} {w['start']}→{w['end']} ===")
        r = run_window(w["tag"], w["start"], w["end"], close_full, base_fund, base_report)
        if not r:
            print("  übersprungen (zu wenig Tage)")
            continue
        path = out / f"v41_{w['tag']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        e = r["end"]
        print(
            f"  Vermögen {e['wealth']:.2f} ({e['return_pct']:+.2f}%) "
            f"Lohn {e['external_profit']:.2f} Trades {e['trades']}"
        )
        summary_rows.append(
            {
                "tag": w["tag"],
                "period": r["period"],
                "wealth": e["wealth"],
                "return_pct": e["return_pct"],
                "external_profit": e["external_profit"],
                "cash": e["cash"],
                "trades": e["trades"],
                "file": path.name,
            }
        )

    summary = {
        "version": "v4.1",
        "features": [
            "Kursverlauf 5/10/20 Tage im Score",
            "Dauerfall-Abzug, Erholungs-Bonus",
            "TP 4.5% + Trailing",
            "Regime-Filter",
        ],
        "windows": summary_rows,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(out / "v41_multi_windows_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nSummary → v41_multi_windows_summary.json")


if __name__ == "__main__":
    main()
