#!/usr/bin/env python3
"""Kurz-Walk-Forward letzte ~14 Tage – v3 mit Momentum."""
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


def main():
    cfg = load_cfg()
    tickers = list(cfg["universe"])
    end = datetime.now()
    start = end - timedelta(days=21)

    close = fetch_close(tickers, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    days = list(close.index)
    if len(days) < 3:
        raise RuntimeError(f"Zu wenig Tage: {len(days)}")

    # Basis-Analyse einmal
    base_fund, base_report = {}, {}
    print("=== Analyse ===")
    for t in tickers:
        base_fund[t] = get_fundamentals(t)
        base_report[t] = get_financial_report(t)
        sc0, _, rtext = score_combined(base_fund[t], base_report[t])
        print(f"{t} Basis-Score ~{sc0:+.1f}: {rtext[:100]}")

    pf = Portfolio(
        starting_cash=float(cfg.get("starting_cash", 10_000)),
        fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
    )
    mem = AlgoMemory()
    ctx = RunContext(cooldown_days=5)
    p0 = prices_on(close, days[0])
    per = pf.cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason="Start equal-weight")

    daily_log = []
    last_week = days[0].isocalendar()[1]
    analysis_end = {}

    for day in days[1:]:
        prices = prices_on(close, day)
        if not prices:
            continue

        scores, details = {}, {}
        for t in set(list(prices.keys()) + list(pf.positions.keys())):
            mom = momentum_at(close, t, day)
            sc, parts, rtext = score_combined(
                base_fund.get(t) or get_fundamentals(t),
                base_report.get(t),
                momentum=mom,
            )
            scores[t] = sc
            details[t] = parts
            analysis_end[t] = {
                "score": round(sc, 1),
                "momentum": mom,
                "findings": (base_report.get(t) or {}).get("findings"),
            }

        iso_w = day.isocalendar()[1]
        full = iso_w != last_week or day == days[-1]
        decisions = decide(
            pf, prices, scores, details, cfg, day.to_pydatetime(), mem, False, ctx
        )
        if not full:
            decisions = [d for d in decisions if d.action == "take_profit"]
        else:
            last_week = iso_w

        entries = apply_decisions(
            pf, decisions, prices, day.to_pydatetime(), mem, ctx
        )
        pf.update_prices(prices)
        daily_log.append(
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
                ],
            }
        )

    result = {
        "version": 3,
        "period": {
            "start": str(days[0].date()),
            "end": str(days[-1].date()),
            "trading_days": len(days),
        },
        "analysis": analysis_end,
        "start_cash": pf.starting_cash,
        "end": {
            "wealth": round(pf.total_wealth(), 2),
            "equity": round(pf.total_equity(), 2),
            "cash": round(pf.cash, 2),
            "external_profit": round(pf.external_profit, 2),
            "return_pct": round((pf.total_wealth() / pf.starting_cash - 1) * 100, 2),
            "trades": len(pf.trades),
            "positions": {k: v.to_dict() for k, v in pf.positions.items()},
        },
        "daily": daily_log,
        "improvements": [
            "Momentum im Score",
            "Kein Nachschuss wenn P&L < -3%",
            "Cooldown 5 Tage nach Teilverkauf",
            "Strengerer Score für Reinvest/Neukauf",
            "Schulden-Anstieg stärker bestraft",
        ],
    }
    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "recent_2w.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    e = result["end"]
    print("=" * 50)
    print(f"{result['period']['start']} → {result['period']['end']}")
    print(f"Vermögen {e['wealth']:.2f} € ({e['return_pct']:+.2f}%) | Lohn {e['external_profit']:.2f} €")
    print(f"Trades {e['trades']} → {path}")


if __name__ == "__main__":
    main()
