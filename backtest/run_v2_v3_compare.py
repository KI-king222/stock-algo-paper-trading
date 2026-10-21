#!/usr/bin/env python3
"""
Vergleich Algo v2 vs v3 – 4 Wochen (anderes Fenster, nicht die letzten Wochen).
Aktuell: März/April 2025.
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

# Anderes Fenster (~1,5 Jahre zurück): März–April 2025
PERIOD_START = "2025-03-03"
PERIOD_END = "2025-03-31"
TAG = "2025-03"

CFG_V2 = {
    "mode": "active_income_v2",
    "starting_cash": 10000.0,
    "fee_per_trade": 1.0,
    "take_profit_pct": 6.0,
    "partial_tp_fraction": 0.5,
    "dip_trigger_pct": -10.0,
    "max_dip_buys_per_ticker": 2,
    "max_position_pct_of_equity": 40.0,
    "dip_cash_fraction": 0.30,
    "min_score_for_dip": -20,
    "min_score_for_new_buy": 12,
    "min_score_for_reinvest": 0,
    "min_score_hold_thesis": -35,
    "no_add_if_pnl_below_pct": -100.0,
    "reinvest_idle_cash": True,
    "reinvest_cash_fraction": 0.55,
    "max_new_buys_per_month": 2,
    "min_monthly_skim_pct": 4.0,
    "monthly_skim_fraction": 0.35,
    "memory_enabled": True,
    "universe": ["SAP.DE", "HNR1.DE", "MUV2.DE", "ENR.DE"],
    "use_momentum": False,
    "cooldown_days": 0,
}

CFG_V3 = {
    "mode": "active_income_v3",
    "starting_cash": 10000.0,
    "fee_per_trade": 1.0,
    "take_profit_pct": 6.0,
    "partial_tp_fraction": 0.4,
    "dip_trigger_pct": -12.0,
    "max_dip_buys_per_ticker": 2,
    "max_position_pct_of_equity": 40.0,
    "dip_cash_fraction": 0.30,
    "min_score_for_dip": -15,
    "min_score_for_new_buy": 25,
    "min_score_for_reinvest": 30,
    "min_score_hold_thesis": -35,
    "no_add_if_pnl_below_pct": -3.0,
    "reinvest_idle_cash": True,
    "reinvest_cash_fraction": 0.40,
    "max_new_buys_per_month": 1,
    "min_monthly_skim_pct": 5.0,
    "monthly_skim_fraction": 0.30,
    "memory_enabled": True,
    "universe": ["SAP.DE", "HNR1.DE", "MUV2.DE", "ENR.DE"],
    "use_momentum": True,
    "cooldown_days": 5,
}


def fetch_close(tickers: List[str], start: str, end: str) -> pd.DataFrame:
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


def prices_on(close: pd.DataFrame, day: pd.Timestamp) -> Dict[str, float]:
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


def momentum_at(close: pd.DataFrame, ticker: str, day: pd.Timestamp) -> Dict[str, Optional[float]]:
    if ticker not in close.columns:
        return {"m5": None, "m10": None}
    s = close[ticker].loc[:day].dropna()
    if len(s) < 3:
        return {"m5": None, "m10": None}
    last = float(s.iloc[-1])
    m5 = (last / float(s.iloc[-5]) - 1) * 100 if len(s) >= 5 else None
    m10 = (last / float(s.iloc[-10]) - 1) * 100 if len(s) >= 10 else None
    return {"m5": m5, "m10": m10}


def run_version(label, cfg, close, base_fund, base_report):
    days = list(close.index)
    pf = Portfolio(
        starting_cash=float(cfg["starting_cash"]),
        fee_per_trade=float(cfg["fee_per_trade"]),
    )
    mem = AlgoMemory() if cfg.get("memory_enabled") else None
    ctx = RunContext(cooldown_days=int(cfg.get("cooldown_days") or 0))
    use_mom = bool(cfg.get("use_momentum"))

    p0 = prices_on(close, days[0])
    per = pf.cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason=f"{label}: Start")

    daily = []
    last_week = days[0].isocalendar()[1]

    for day in days[1:]:
        prices = prices_on(close, day)
        if not prices:
            continue
        scores, details = {}, {}
        for t in set(list(prices.keys()) + list(pf.positions.keys())):
            mom = momentum_at(close, t, day) if use_mom else None
            sc, parts, _ = score_combined(
                base_fund.get(t) or {}, base_report.get(t), momentum=mom
            )
            if not use_mom and "momentum" in parts:
                sc = sc - float(parts.get("momentum") or 0)
                parts = {**parts, "momentum": 0.0, "total": round(sc, 1)}
            scores[t] = sc
            details[t] = parts

        iso_w = day.isocalendar()[1]
        full = iso_w != last_week or day == days[-1]
        decisions = decide(
            pf, prices, scores, details, cfg, day.to_pydatetime(), mem, False,
            ctx if cfg.get("cooldown_days") else None,
        )
        if not full:
            decisions = [d for d in decisions if d.action == "take_profit"]
        else:
            last_week = iso_w

        entries = apply_decisions(
            pf, decisions, prices, day.to_pydatetime(), mem,
            ctx if cfg.get("cooldown_days") else None,
        )
        pf.update_prices(prices)
        daily.append(
            {
                "date": str(day.date()),
                "wealth": round(pf.total_wealth(), 2),
                "equity": round(pf.total_equity(), 2),
                "cash": round(pf.cash, 2),
                "external_profit": round(pf.external_profit, 2),
                "actions": [
                    {
                        "ticker": e.get("ticker"),
                        "aktion": e.get("aktion"),
                        "stück": e.get("stück"),
                        "grund": e.get("grund"),
                        "score": e.get("score"),
                    }
                    for e in entries
                    if e.get("aktion") not in ("halten",) or e.get("stück")
                ],
            }
        )

    return {
        "version": label,
        "period": {
            "start": str(days[0].date()),
            "end": str(days[-1].date()),
            "trading_days": len(days),
        },
        "config": {k: cfg[k] for k in cfg if k != "universe"},
        "universe": cfg["universe"],
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
        "note": "Kurse walk-forward; Fundamentals/Bericht = Yahoo-Proxy.",
    }


def main():
    tickers = CFG_V3["universe"]
    start_buf = (datetime.fromisoformat(PERIOD_START) - timedelta(days=20)).strftime("%Y-%m-%d")
    close_full = fetch_close(tickers, start_buf, PERIOD_END)
    close = close_full.loc[PERIOD_START:PERIOD_END]
    if len(close) < 5:
        raise RuntimeError(f"Zu wenig Tage: {len(close)}")

    base_fund, base_report = {}, {}
    for t in tickers:
        base_fund[t] = get_fundamentals(t)
        base_report[t] = get_financial_report(t)

    r2 = run_version("v2", CFG_V2, close, base_fund, base_report)
    r3 = run_version("v3", CFG_V3, close, base_fund, base_report)

    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    path2 = out / f"compare_v2_4w_{TAG}.json"
    path3 = out / f"compare_v3_4w_{TAG}.json"
    path_sum = out / f"compare_v2_v3_4w_{TAG}_summary.json"

    with open(path2, "w", encoding="utf-8") as f:
        json.dump(r2, f, indent=2, ensure_ascii=False)
    with open(path3, "w", encoding="utf-8") as f:
        json.dump(r3, f, indent=2, ensure_ascii=False)

    summary = {
        "period": r2["period"],
        "v2": r2["end"],
        "v3": r3["end"],
        "delta_wealth_v3_minus_v2": round(r3["end"]["wealth"] - r2["end"]["wealth"], 2),
        "delta_lohn_v3_minus_v2": round(
            r3["end"]["external_profit"] - r2["end"]["external_profit"], 2
        ),
        "files": {"v2": path2.name, "v3": path3.name},
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(path_sum, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # auch unter generischem Namen fürs Dashboard
    with open(out / "compare_v2_v3_4w_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"Zeitraum: {r2['period']['start']} → {r2['period']['end']}")
    print(f"v2: {r2['end']['wealth']:.2f} ({r2['end']['return_pct']:+.2f}%) Lohn {r2['end']['external_profit']:.2f}")
    print(f"v3: {r3['end']['wealth']:.2f} ({r3['end']['return_pct']:+.2f}%) Lohn {r3['end']['external_profit']:.2f}")
    print(f"→ {path2.name}, {path3.name}")


if __name__ == "__main__":
    main()
