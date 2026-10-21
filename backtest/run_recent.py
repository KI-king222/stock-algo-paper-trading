#!/usr/bin/env python3
"""
Kurz-Walk-Forward: letzte ~14 Handelstage.
Täglich: Kurse bis T, Score aus Fundamentals+Bericht (point-in-time nur Kurse;
Fundamentals/Bericht = aktuelle Yahoo-Daten als Proxy – bei 2 Wochen akzeptabel).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portfolio import Portfolio  # noqa: E402
from data import get_financial_report, get_fundamentals  # noqa: E402
from algo.scoring import score_combined  # noqa: E402
from algo.decision_engine import apply_decisions, decide  # noqa: E402
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


def main():
    cfg = load_cfg()
    tickers = list(cfg["universe"])
    end = datetime.now()
    start = end - timedelta(days=21)  # ~2–3 Wochen Kalender → ~10–14 Handelstage

    close = fetch_close(
        tickers, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    days = list(close.index)
    if len(days) < 3:
        raise RuntimeError(f"Zu wenig Handelstage: {len(days)}")

    # Scores einmal (Fundamentals ändern sich nicht täglich in 2 Wochen)
    scores, details, reports = {}, {}, {}
    print("=== Analyse (Fundamentals + Quartalsbericht) ===")
    for t in tickers:
        fund = get_fundamentals(t)
        report = get_financial_report(t)
        sc, parts, rtext = score_combined(fund, report)
        scores[t] = sc
        details[t] = parts
        reports[t] = {"score": round(sc, 1), "findings": report.get("findings"), "text": rtext}
        print(f"\n{t} Score {sc:+.1f}")
        for line in report.get("findings") or []:
            print(f"  · {line}")

    pf = Portfolio(
        starting_cash=float(cfg.get("starting_cash", 10_000)),
        fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
    )
    mem = AlgoMemory()
    p0 = prices_on(close, days[0])
    per = pf.cash / max(len(p0), 1)
    for t, p in p0.items():
        n = int((per - pf.fee_per_trade) // p)
        if n > 0:
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason="Start equal-weight")

    daily_log = []
    last_week = days[0].isocalendar()[1]

    for day in days[1:]:
        prices = prices_on(close, day)
        if not prices:
            continue
        iso_w = day.isocalendar()[1]
        new_week = iso_w != last_week
        # Jeden Tag TP prüfen; volle Logik am Wochenwechsel + letzter Tag
        full = new_week or day == days[-1]
        decisions = decide(
            pf,
            prices,
            scores,
            details,
            cfg,
            day.to_pydatetime(),
            memory=mem,
            force_monthly_skim=False,
        )
        if not full:
            decisions = [d for d in decisions if d.action == "take_profit"]
        else:
            last_week = iso_w

        entries = apply_decisions(pf, decisions, prices, day.to_pydatetime(), mem)
        pf.update_prices(prices)

        acted = [e for e in entries if e.get("aktion") not in ("halten",) or e.get("stück")]
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
        "period": {
            "start": str(days[0].date()),
            "end": str(days[-1].date()),
            "trading_days": len(days),
        },
        "analysis": reports,
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
        "trades": [t.to_dict() for t in pf.trades],
        "note": (
            "Fundamentals/Bericht = aktuelle Yahoo-Daten (bei 2 Wochen ok). "
            "Kurse strikt walk-forward ohne Look-ahead."
        ),
    }

    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "recent_2w.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 50)
    print(f"Zeitraum: {result['period']['start']} → {result['period']['end']} "
          f"({result['period']['trading_days']} Tage)")
    e = result["end"]
    print(f"Vermögen: {e['wealth']:,.2f} € ({e['return_pct']:+.2f}%)")
    print(f"Lohn realisiert: {e['external_profit']:,.2f} € | Cash: {e['cash']:,.2f} €")
    print(f"Trades: {e['trades']}")
    print(f"→ {path}")


if __name__ == "__main__":
    main()
