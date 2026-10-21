#!/usr/bin/env python3
"""
5-Jahres Walk-Forward für den Algorithmus.

Wichtig ehrlich:
- Kurse: nur bis Tag T (kein Look-ahead).
- Score: historisch aus Kursfenstern (Momentum, Drawdown, Volatilität)
  als Proxy – yfinance.info wäre HEUTIGE Fundamentals und würde
  die Vergangenheit verfälschen. Echte Point-in-Time-Bilanzen kommen später.
- Regeln: identisch zu config (TP 12%, Dip -12%, max 2, kein Minus-Sell).
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
    """
    Point-in-time Score nur aus Kursen bis day.
    Proxy für 'Qualität/Trend' – nicht echte Bilanz.
    """
    if ticker not in close.columns:
        return 0.0, {"total": 0.0, "note": "no data"}
    s = close[ticker].loc[:day].dropna()
    if len(s) < 40:
        return 0.0, {"total": 0.0, "note": "too short"}

    last = float(s.iloc[-1])
    # 60d / 120d Momentum
    m60 = (last / float(s.iloc[-60]) - 1) * 100 if len(s) >= 60 else 0.0
    m120 = (last / float(s.iloc[-120]) - 1) * 100 if len(s) >= 120 else m60
    # Drawdown vom 120d-Hoch
    window = s.iloc[-120:] if len(s) >= 120 else s
    peak = float(window.max())
    dd = (last / peak - 1) * 100 if peak > 0 else 0.0
    # Volatilität (20d)
    ret = s.pct_change().dropna().iloc[-20:]
    vol = float(ret.std() * (252 ** 0.5) * 100) if len(ret) > 5 else 20.0

    # Teil-Scores ähnlich scoring.py-Skala
    growth = max(-25, min(25, m60 * 0.4 + m120 * 0.2))
    # Stabilität: moderater DD und nicht extreme Vol
    bal = 0.0
    if dd > -15:
        bal += 10
    elif dd > -30:
        bal += 0
    else:
        bal -= 15
    if vol < 25:
        bal += 8
    elif vol > 45:
        bal -= 10
    bal = max(-25, min(25, bal))
    # Bewertung-Proxy: starkes Momentum + tiefer DD = teuer gelaufen → leicht negativ
    val = 0.0
    if m120 > 40 and dd > -8:
        val = -5
    elif m120 < -20:
        val = 5  # günstiger Einstieg möglich

    total = max(-100, min(100, growth + bal + val))
    parts = {
        "growth": round(growth, 1),
        "balance": round(bal, 1),
        "valuation": round(val, 1),
        "m60": round(m60, 1),
        "dd": round(dd, 1),
        "vol": round(vol, 1),
        "total": round(total, 1),
        "source": "price_proxy_pit",
    }
    return total, parts


def max_drawdown(history: List[dict]) -> float:
    peak = 0.0
    mdd = 0.0
    for h in history:
        w = float(h.get("wealth") or h.get("equity") or 0)
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
    if len(days) < 50:
        raise RuntimeError("Zu wenige Handelstage")

    pf = Portfolio(
        starting_cash=float(cfg.get("starting_cash", 10_000)),
        fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
    )

    # Tag 1 equal-weight
    p0 = prices_on(close, days[0])
    per = pf.cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason="BT: equal-weight Start")

    last_month = days[0].month
    last_year = days[0].year
    equity_hist = []
    monthly_runs = 0
    tp_n = dip_n = new_n = 0

    for day in days[1:]:
        prices = prices_on(close, day)
        if not prices:
            continue
        if day.year != last_year:
            pf.freistellung_remaining = pf.freistellung_yearly
            last_year = day.year

        # Täglich: Scores + Entscheidungen (TP kann täglich greifen)
        scores = {}
        details = {}
        for t in set(list(prices.keys()) + list(pf.positions.keys())):
            sc, parts = historical_score(close, t, day)
            scores[t] = sc
            details[t] = parts

        # Monatlich aggressiver New/Dip; täglich nur TP+Dip-Check
        # Für Performance: decide täglich (TP wichtig täglich)
        decisions = decide(pf, prices, scores, details, cfg, day.to_pydatetime())

        # An Nicht-Monats-Tagen nur take_profit erlauben (weniger Overtrading)
        if day.month == last_month:
            decisions = [d for d in decisions if d.action == "take_profit"]
        else:
            monthly_runs += 1
            last_month = day.month

        log = apply_decisions(pf, decisions, prices, day.to_pydatetime())
        for e in log:
            if e.get("action") == "sell_tp" and e.get("ok"):
                tp_n += 1
            elif e.get("action") == "dip_buy" and e.get("ok"):
                dip_n += 1
            elif e.get("action") == "new_buy" and e.get("ok"):
                new_n += 1

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
        "config": {
            "start": start,
            "end": end,
            "universe": tickers,
            "take_profit_pct": cfg.get("take_profit_pct"),
            "dip_trigger_pct": cfg.get("dip_trigger_pct"),
            "score_mode": "price_proxy_point_in_time",
            "note": "Kein Look-ahead auf heutige yfinance.info; Score aus Kursfenstern bis T",
        },
        "strategy": {
            "final_equity": round(pf.total_equity(), 2),
            "external_profit": round(pf.external_profit, 2),
            "total_wealth": round(wealth, 2),
            "cash": round(pf.cash, 2),
            "taxes_paid_total": round(pf.taxes_paid_total, 2),
            "return_wealth_pct": round((wealth / pf.starting_cash - 1) * 100, 2),
            "trades": len(pf.trades),
            "take_profit_events": tp_n,
            "dip_buy_events": dip_n,
            "new_buy_events": new_n,
            "monthly_decision_days": monthly_runs,
            "open_positions": {k: v.to_dict() for k, v in pf.positions.items()},
        },
        "benchmark_buy_hold": bench,
        "max_drawdown_pct": max_drawdown(equity_hist),
        "equity_points": len(equity_hist),
    }
    return result, pf, equity_hist


def main():
    print("=" * 60)
    print("Algo 5y Walk-Forward Backtest")
    print("=" * 60)
    result, pf, hist = run()
    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(out / f"summary_{stamp}.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(out / "latest_summary.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    if hist:
        pd.DataFrame(hist).to_csv(out / f"equity_{stamp}.csv", index=False)

    s = result["strategy"]
    b = result["benchmark_buy_hold"]
    print(f"Vermögen:   {s['total_wealth']:,.2f} € ({s['return_wealth_pct']:+.2f}%)")
    print(f"Equity:     {s['final_equity']:,.2f} € | external {s['external_profit']:,.2f} €")
    print(f"Drawdown:   {result['max_drawdown_pct']}%")
    print(f"Trades:     {s['trades']} | TP {s['take_profit_events']} | Dip {s['dip_buy_events']} | New {s['new_buy_events']}")
    print(f"Buy&Hold:   {b['final_value']:,.2f} € ({b['return_pct']:+.2f}%)")
    print(f"→ {out / 'latest_summary.json'}")

    # Vergleich zu bekannten KI-Repo-Regel-Läufen
    comparison = {
        "algo_5y": s,
        "benchmark": b,
        "reference_from_ki_repo": {
            "S1_C_TP8_Dip12": {"wealth": 17294.82, "return_pct": 72.95, "dd": -39.25},
            "S1_A_TP12_Dip8": {"wealth": 16938.57, "return_pct": 69.39, "dd": -32.85},
            "S2_brain_proxy": {"wealth": 12811.99, "return_pct": 28.12, "dd": -44.65},
        },
        "max_drawdown_pct": result["max_drawdown_pct"],
    }
    with open(out / "comparison.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
