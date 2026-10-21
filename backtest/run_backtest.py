#!/usr/bin/env python3
"""
5-Jahres Walk-Forward Algo v2.
Score point-in-time aus Kursfenstern (kein Look-ahead auf heutige Bilanzen).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import Portfolio  # noqa: E402
from algo.decision_engine import apply_decisions, decide  # noqa: E402


def load_cfg() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


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


def historical_score(close: pd.DataFrame, ticker: str, day: pd.Timestamp) -> Tuple[float, dict]:
    """v2: besseres Dip-Signal – überverkauft + nicht total gebrochen = höherer Score."""
    if ticker not in close.columns:
        return 0.0, {"total": 0.0, "note": "no data"}
    s = close[ticker].loc[:day].dropna()
    if len(s) < 40:
        return 0.0, {"total": 0.0, "note": "too short"}

    last = float(s.iloc[-1])
    m20 = (last / float(s.iloc[-20]) - 1) * 100 if len(s) >= 20 else 0.0
    m60 = (last / float(s.iloc[-60]) - 1) * 100 if len(s) >= 60 else 0.0
    m120 = (last / float(s.iloc[-120]) - 1) * 100 if len(s) >= 120 else m60
    window = s.iloc[-120:] if len(s) >= 120 else s
    peak = float(window.max())
    dd = (last / peak - 1) * 100 if peak > 0 else 0.0
    ret = s.pct_change().dropna().iloc[-20:]
    vol = float(ret.std() * (252 ** 0.5) * 100) if len(ret) > 5 else 20.0

    # Trend-Qualität (mittel-/langfristig)
    trend = 0.0
    if m120 > 10:
        trend += 12
    elif m120 > 0:
        trend += 5
    elif m120 > -15:
        trend += 0
    else:
        trend -= 12
    if m60 > 5:
        trend += 6
    elif m60 < -25:
        trend -= 8
    trend = max(-25, min(25, trend))

    # Mean-Reversion-Bonus bei gesundem Dip (nicht Crash)
    dip_quality = 0.0
    if -25 <= dd <= -10 and m120 > -10:
        dip_quality = 12  # attraktiver Rücksetzer im Aufwärtstrend
    elif dd < -40 and m120 < -20:
        dip_quality = -15  # strukturell schwach
    elif -15 <= m20 <= -5 and m60 > -10:
        dip_quality = 6

    # Volatilität
    vol_score = 0.0
    if vol < 22:
        vol_score = 8
    elif vol < 35:
        vol_score = 2
    elif vol > 50:
        vol_score = -12
    else:
        vol_score = -4

    total = max(-100, min(100, trend + dip_quality + vol_score))
    parts = {
        "trend": round(trend, 1),
        "dip_quality": round(dip_quality, 1),
        "vol_score": round(vol_score, 1),
        "m20": round(m20, 1),
        "m60": round(m60, 1),
        "m120": round(m120, 1),
        "dd": round(dd, 1),
        "vol": round(vol, 1),
        "total": round(total, 1),
        "source": "price_proxy_v2",
    }
    return total, parts


def max_drawdown(history: List[dict]) -> float:
    peak = 0.0
    mdd = 0.0
    for h in history:
        w = float(h.get("wealth") or 0)
        if w > peak:
            peak = w
        if peak > 0:
            dd = (w - peak) / peak * 100
            if dd < mdd:
                mdd = dd
    return round(mdd, 2)


def buy_hold(close: pd.DataFrame, cash0: float, fee: float) -> dict:
    first, last = close.index[0], close.index[-1]
    p0 = prices_on(close, first)
    p1 = prices_on(close, last)
    cash = cash0
    hold = {}
    per = cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - fee) // p)
        if n <= 0:
            continue
        cash -= n * p + fee
        hold[t] = float(n)
    val = cash + sum(hold.get(t, 0) * p1.get(t, 0) for t in hold)
    return {
        "final_value": round(val, 2),
        "return_pct": round((val / cash0 - 1) * 100, 2),
        "holdings_end": hold,
        "cash_end": round(cash, 2),
    }


def run(start="2021-10-20", end="2026-10-20") -> dict:
    cfg = load_cfg()
    tickers = list(cfg["universe"])
    close = fetch_close(tickers, start, end)
    days = list(close.index)

    pf = Portfolio(
        starting_cash=float(cfg.get("starting_cash", 10_000)),
        fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
    )
    p0 = prices_on(close, days[0])
    per = pf.cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason="BT: equal-weight")

    last_month = days[0].month
    last_year = days[0].year
    equity_hist = []
    monthly_runs = 0
    counts = {"sell_tp": 0, "dip_buy": 0, "new_buy": 0, "reinvest": 0}

    for day in days[1:]:
        prices = prices_on(close, day)
        if not prices:
            continue
        if day.year != last_year:
            pf.freistellung_remaining = pf.freistellung_yearly
            last_year = day.year

        scores, details = {}, {}
        for t in set(list(prices.keys()) + list(pf.positions.keys())):
            sc, parts = historical_score(close, t, day)
            scores[t] = sc
            details[t] = parts

        decisions = decide(pf, prices, scores, details, cfg, day.to_pydatetime())
        # Täglich nur TP; voller Algo-Monat
        if day.month == last_month:
            decisions = [d for d in decisions if d.action == "take_profit"]
        else:
            monthly_runs += 1
            last_month = day.month

        log = apply_decisions(pf, decisions, prices, day.to_pydatetime())
        for e in log:
            a = e.get("action")
            if e.get("ok") and a in counts:
                counts[a] += 1

        pf.update_prices(prices)
        equity_hist.append(
            {
                "date": str(day.date()),
                "equity": pf.total_equity(),
                "wealth": pf.total_wealth(),
                "cash": pf.cash,
            }
        )

    wealth = pf.total_wealth()
    bench = buy_hold(close, pf.starting_cash, pf.fee_per_trade)
    result = {
        "version": 2,
        "config": {
            "start": start,
            "end": end,
            "universe": tickers,
            "take_profit_pct": cfg.get("take_profit_pct"),
            "dip_trigger_pct": cfg.get("dip_trigger_pct"),
            "score_mode": "price_proxy_v2",
            "note": "Kein Look-ahead; Dip-Qualität im Score; Reinvest in offene Pos",
        },
        "strategy": {
            "final_equity": round(pf.total_equity(), 2),
            "external_profit": round(pf.external_profit, 2),
            "total_wealth": round(wealth, 2),
            "cash": round(pf.cash, 2),
            "taxes_paid_total": round(pf.taxes_paid_total, 2),
            "return_wealth_pct": round((wealth / pf.starting_cash - 1) * 100, 2),
            "trades": len(pf.trades),
            "take_profit_events": counts["sell_tp"],
            "dip_buy_events": counts["dip_buy"],
            "new_buy_events": counts["new_buy"],
            "reinvest_events": counts["reinvest"],
            "monthly_decision_days": monthly_runs,
            "open_positions": {k: v.to_dict() for k, v in pf.positions.items()},
        },
        "benchmark_buy_hold": bench,
        "max_drawdown_pct": max_drawdown(equity_hist),
        "equity_points": len(equity_hist),
    }
    return result, pf


def main():
    print("Algo 5y Backtest v2")
    result, pf = run()
    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(out / f"summary_{stamp}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(out / "latest_summary.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    s = result["strategy"]
    b = result["benchmark_buy_hold"]
    comparison = {
        "algo_v1": {"wealth": 15382.17, "return_pct": 53.82, "dd": -26.46},
        "algo_v2": {
            "wealth": s["total_wealth"],
            "return_pct": s["return_wealth_pct"],
            "dd": result["max_drawdown_pct"],
            "trades": s["trades"],
            "tp": s["take_profit_events"],
            "dip": s["dip_buy_events"],
            "new": s["new_buy_events"],
            "reinvest": s["reinvest_events"],
            "cash": s["cash"],
        },
        "ref_rules_S1_C": {"wealth": 17294.82, "return_pct": 72.95, "dd": -39.25},
        "ref_rules_S1_A": {"wealth": 16938.57, "return_pct": 69.39, "dd": -32.85},
        "buy_hold": b,
    }
    with open(out / "comparison.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, ensure_ascii=False)

    print(f"Vermögen: {s['total_wealth']:,.2f} € ({s['return_wealth_pct']:+.2f}%)")
    print(f"DD: {result['max_drawdown_pct']}% | Trades {s['trades']} | TP {s['take_profit_events']} Dip {s['dip_buy_events']} New {s['new_buy_events']} Reinvest {s['reinvest_events']}")
    print(f"Cash: {s['cash']:,.2f} € | BH: {b['final_value']:,.2f} € ({b['return_pct']:+.2f}%)")


if __name__ == "__main__":
    main()
