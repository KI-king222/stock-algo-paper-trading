#!/usr/bin/env python3
"""Active-Income Walk-Forward + Memory."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import Portfolio  # noqa: E402
from algo.decision_engine import apply_decisions, decide  # noqa: E402
from algo.memory import AlgoMemory  # noqa: E402

_CLOSE_CACHE: Dict[tuple, pd.DataFrame] = {}


def load_cfg() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_close(tickers: List[str], start: str, end: str) -> pd.DataFrame:
    key = (tuple(tickers), start, end)
    if key in _CLOSE_CACHE:
        return _CLOSE_CACHE[key]
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
    close = close.reindex(columns=[t for t in tickers if t in close.columns])
    _CLOSE_CACHE[key] = close
    return close


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
    if ticker not in close.columns:
        return 0.0, {"total": 0.0}
    s = close[ticker].loc[:day].dropna()
    if len(s) < 40:
        return 0.0, {"total": 0.0}
    last = float(s.iloc[-1])
    m20 = (last / float(s.iloc[-20]) - 1) * 100 if len(s) >= 20 else 0.0
    m60 = (last / float(s.iloc[-60]) - 1) * 100 if len(s) >= 60 else 0.0
    m120 = (last / float(s.iloc[-120]) - 1) * 100 if len(s) >= 120 else m60
    window = s.iloc[-120:] if len(s) >= 120 else s
    peak = float(window.max())
    dd = (last / peak - 1) * 100 if peak > 0 else 0.0
    ret = s.pct_change().dropna().iloc[-20:]
    vol = float(ret.std() * (252 ** 0.5) * 100) if len(ret) > 5 else 20.0

    trend = 0.0
    if m120 > 10:
        trend += 12
    elif m120 > 0:
        trend += 5
    elif m120 <= -15:
        trend -= 12
    if m60 > 5:
        trend += 6
    elif m60 < -25:
        trend -= 8
    trend = max(-25, min(25, trend))

    dip_quality = 0.0
    if -25 <= dd <= -10 and m120 > -10:
        dip_quality = 12
    elif dd < -40 and m120 < -20:
        dip_quality = -15

    vol_score = 8 if vol < 22 else (2 if vol < 35 else (-12 if vol > 50 else -4))
    total = max(-100, min(100, trend + dip_quality + vol_score))
    return total, {
        "trend": round(trend, 1),
        "dip_quality": round(dip_quality, 1),
        "m60": round(m60, 1),
        "dd": round(dd, 1),
        "total": round(total, 1),
    }


def max_drawdown(history: List[dict]) -> float:
    peak = mdd = 0.0
    for h in history:
        w = float(h.get("wealth") or 0)
        peak = max(peak, w)
        if peak > 0:
            mdd = min(mdd, (w - peak) / peak * 100)
    return round(mdd, 2)


def buy_hold(close: pd.DataFrame, cash0: float, fee: float) -> dict:
    first, last = close.index[0], close.index[-1]
    p0, p1 = prices_on(close, first), prices_on(close, last)
    cash, hold = cash0, {}
    per = cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - fee) // p)
        if n > 0:
            cash -= n * p + fee
            hold[t] = float(n)
    val = cash + sum(hold.get(t, 0) * p1.get(t, 0) for t in hold)
    return {"final_value": round(val, 2), "return_pct": round((val / cash0 - 1) * 100, 2)}


def run(start="2021-10-20", end="2026-10-20", cfg: Optional[dict] = None):
    cfg = cfg if cfg is not None else load_cfg()
    tickers = list(cfg["universe"])
    close = fetch_close(tickers, start, end)
    days = list(close.index)
    mem = AlgoMemory() if cfg.get("memory_enabled", True) else None

    pf = Portfolio(
        starting_cash=float(cfg.get("starting_cash", 10_000)),
        fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
    )
    p0 = prices_on(close, days[0])
    per = pf.cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason="BT start")

    last_month = days[0].month
    last_week = days[0].isocalendar()[1]
    last_year = days[0].year
    equity_hist = []
    monthly_payouts: Dict[str, float] = {}
    prev_external = 0.0
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

        iso_week = day.isocalendar()[1]
        new_month = day.month != last_month
        new_week = iso_week != last_week

        # Active: TP wöchentlich; voller Entscheidungszyklus monatlich
        weekly_tp = bool(cfg.get("weekly_tp_check", True))
        if new_month:
            decisions = decide(
                pf, prices, scores, details, cfg, day.to_pydatetime(), mem, force_monthly_skim=True
            )
            last_month = day.month
            last_week = iso_week
        elif weekly_tp and new_week:
            decisions = decide(
                pf, prices, scores, details, cfg, day.to_pydatetime(), mem, force_monthly_skim=False
            )
            # nur TP an Wochenwechseln
            decisions = [d for d in decisions if d.action == "take_profit"]
            last_week = iso_week
        else:
            # täglich nur volles TP
            decisions = decide(
                pf, prices, scores, details, cfg, day.to_pydatetime(), mem, force_monthly_skim=False
            )
            decisions = [d for d in decisions if d.action == "take_profit"]

        log = apply_decisions(pf, decisions, prices, day.to_pydatetime(), mem)
        for e in log:
            a = e.get("action")
            if e.get("ok") and a in counts:
                counts[a] += 1

        # Memory: Dips die weiter unter Wasser sind
        if mem and new_month:
            for t, pos in pf.positions.items():
                if pos.dip_buy_count > 0:
                    mem.record_dip_underwater(t, pos.unrealized_pnl_pct < -5)

        # Monats-Lohn = Zuwachs external_profit
        if new_month or day == days[-1]:
            delta = pf.external_profit - prev_external
            if delta > 0.5:
                key = f"{day.year}-{day.month:02d}"
                monthly_payouts[key] = monthly_payouts.get(key, 0.0) + delta
            prev_external = pf.external_profit

        pf.update_prices(prices)
        equity_hist.append(
            {
                "date": str(day.date()),
                "equity": pf.total_equity(),
                "wealth": pf.total_wealth(),
                "external_profit": pf.external_profit,
                "cash": pf.cash,
            }
        )

    wealth = pf.total_wealth()
    months_with_pay = sum(1 for v in monthly_payouts.values() if v > 0)
    total_pay = sum(monthly_payouts.values())
    bench = buy_hold(close, pf.starting_cash, pf.fee_per_trade)

    # Score für Optimizer: Vermögen + Bonus für regelmäßige Auszahlungen
    income_score = wealth + total_pay * 0.35 + months_with_pay * 80

    result = {
        "mode": "active_income",
        "config_snapshot": {
            "take_profit_pct": cfg.get("take_profit_pct"),
            "partial_tp_fraction": cfg.get("partial_tp_fraction"),
            "dip_trigger_pct": cfg.get("dip_trigger_pct"),
            "min_monthly_skim_pct": cfg.get("min_monthly_skim_pct"),
        },
        "strategy": {
            "final_equity": round(pf.total_equity(), 2),
            "external_profit": round(pf.external_profit, 2),
            "total_wealth": round(wealth, 2),
            "cash": round(pf.cash, 2),
            "return_wealth_pct": round((wealth / pf.starting_cash - 1) * 100, 2),
            "trades": len(pf.trades),
            "take_profit_events": counts["sell_tp"],
            "dip_buy_events": counts["dip_buy"],
            "new_buy_events": counts["new_buy"],
            "reinvest_events": counts["reinvest"],
            "total_realized_payouts": round(total_pay, 2),
            "months_with_payout": months_with_pay,
            "monthly_payouts": {k: round(v, 2) for k, v in sorted(monthly_payouts.items())},
            "avg_payout_per_paying_month": round(total_pay / months_with_pay, 2) if months_with_pay else 0.0,
            "income_score": round(income_score, 2),
            "open_positions": {k: v.to_dict() for k, v in pf.positions.items()},
            "memory": mem.to_dict() if mem else {},
        },
        "benchmark_buy_hold": bench,
        "max_drawdown_pct": max_drawdown(equity_hist),
    }
    return result, pf


def main():
    result, pf = run()
    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "latest_summary.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    s = result["strategy"]
    print(
        f"Wealth {s['total_wealth']:.0f} | Lohn sum {s['total_realized_payouts']:.0f} | "
        f"Monate mit Lohn {s['months_with_payout']} | DD {result['max_drawdown_pct']}%"
    )


if __name__ == "__main__":
    main()
