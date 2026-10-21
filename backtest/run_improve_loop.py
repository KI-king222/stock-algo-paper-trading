#!/usr/bin/env python3
"""
Verbesserungs-Schleife auf den 3 bekannten Fenstern.
Ziel-Metrik (ehrlich, kein „jeder Monat +1000“):
  score = 0.5 * min(returns) + 0.3 * mean(returns) + 0.2 * mean(lohn)/100
Sucht per Zufalls-Stichprobe bessere Configs, speichert beste.
"""
from __future__ import annotations

import json
import math
import random
import sys
from copy import deepcopy
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

BASE_CFG = {
    "mode": "active_income_loop",
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
    "cooldown_days": 5,
}

# Suchraum (eng, damit nicht total überfit)
SEARCH = {
    "take_profit_pct": [3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
    "partial_tp_fraction": [0.25, 0.30, 0.35, 0.40, 0.50],
    "trail_giveback_pct": [1.5, 2.0, 2.5, 3.0],
    "reinvest_cash_fraction": [0.15, 0.20, 0.25, 0.30, 0.40],
    "regime_block_buys_if_median_mom5_below": [-4.0, -3.0, -2.0, -1.0, 0.0],
    "no_add_if_pnl_below_pct": [-5.0, -3.0, -1.0, 0.0],
    "min_score_for_reinvest": [20, 25, 30, 35, 40],
    "min_score_for_new_buy": [20, 25, 30, 35],
    "max_position_pct_of_equity": [30.0, 35.0, 40.0],
    "dip_trigger_pct": [-15.0, -12.0, -10.0],
    "cooldown_days": [3, 5, 7],
}

N_TRIALS = 40  # genug für eine Schleife, nicht Endlos


def fetch_close(tickers, start, end):
    import yfinance as yf

    raw = yf.download(
        tickers, start=start, end=end, auto_adjust=True, progress=False, threads=True
    )
    if raw.empty:
        raise RuntimeError("Keine Kurse")
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


def run_one(cfg, close_full, period_start, period_end, base_fund, base_report):
    close = close_full.loc[period_start:period_end]
    days = list(close.index)
    if len(days) < 5:
        return None

    pf = Portfolio(
        starting_cash=float(cfg["starting_cash"]),
        fee_per_trade=float(cfg["fee_per_trade"]),
    )
    mem = AlgoMemory() if cfg.get("memory_enabled") else None
    ctx = RunContext(cooldown_days=int(cfg.get("cooldown_days") or 5))

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
            pf.buy(t, float(n), p, days[0].to_pydatetime(), reason="Start")
            ctx.update_peak(t, p)

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
        apply_decisions(pf, decisions, prices, day.to_pydatetime(), mem, ctx)
        pf.update_prices(prices)

    return {
        "wealth": round(pf.total_wealth(), 2),
        "return_pct": round((pf.total_wealth() / pf.starting_cash - 1) * 100, 2),
        "external_profit": round(pf.external_profit, 2),
        "cash": round(pf.cash, 2),
        "trades": len(pf.trades),
    }


def sample_cfg(rng: random.Random) -> dict:
    cfg = deepcopy(BASE_CFG)
    for k, choices in SEARCH.items():
        cfg[k] = rng.choice(choices)
    return cfg


def metric(window_results: List[dict]) -> float:
    rets = [w["return_pct"] for w in window_results]
    lohne = [w["external_profit"] for w in window_results]
    return 0.5 * min(rets) + 0.3 * (sum(rets) / len(rets)) + 0.2 * (sum(lohne) / len(lohne) / 100.0)


def main():
    rng = random.Random(42)
    tickers = BASE_CFG["universe"]
    starts = [w["start"] for w in WINDOWS]
    ends = [w["end"] for w in WINDOWS]
    buf = (datetime.fromisoformat(min(starts)) - timedelta(days=35)).strftime("%Y-%m-%d")
    close_full = fetch_close(tickers, buf, max(ends))

    base_fund, base_report = {}, {}
    for t in tickers:
        base_fund[t] = get_fundamentals(t)
        base_report[t] = get_financial_report(t)

    # Baseline = aktuelle BASE_CFG
    history = []
    best_cfg = deepcopy(BASE_CFG)
    best_score = -1e9
    best_windows = None

    def eval_cfg(cfg, label):
        rows = []
        for w in WINDOWS:
            r = run_one(
                cfg, close_full, w["start"], w["end"], base_fund, base_report
            )
            if r is None:
                return None, None
            rows.append({"tag": w["tag"], **r})
        sc = metric(rows)
        print(f"{label}: score={sc:.3f} | " + " | ".join(
            f"{x['tag']} {x['return_pct']:+.2f}% Lohn {x['external_profit']:.0f}" for x in rows
        ))
        return sc, rows

    print("=== Baseline ===")
    sc0, rows0 = eval_cfg(BASE_CFG, "baseline")
    best_score, best_windows, best_cfg = sc0, rows0, deepcopy(BASE_CFG)
    history.append({"trial": 0, "score": sc0, "windows": rows0, "cfg": deepcopy(BASE_CFG)})

    for i in range(1, N_TRIALS + 1):
        cfg = sample_cfg(rng)
        sc, rows = eval_cfg(cfg, f"trial {i}")
        if sc is None:
            continue
        history.append({"trial": i, "score": sc, "windows": rows, "cfg": {
            k: cfg[k] for k in SEARCH
        }})
        if sc > best_score:
            best_score = sc
            best_windows = rows
            best_cfg = deepcopy(cfg)
            print(f"  ★ neues Best score={best_score:.3f}")

    out = ROOT / "backtest" / "results"
    out.mkdir(parents=True, exist_ok=True)

    # beste Config ins Live-config schreiben? Nur als Vorschlag speichern
    result = {
        "note": (
            "Optimiert NUR auf diesen 3 Fenstern → Overfitting-Risiko. "
            "Kein Anspruch auf jeden Monat +500/1000."
        ),
        "n_trials": N_TRIALS,
        "metric": "0.5*min(ret)+0.3*mean(ret)+0.2*mean(lohn)/100",
        "baseline": {"score": sc0, "windows": rows0},
        "best": {
            "score": best_score,
            "windows": best_windows,
            "cfg_overrides": {k: best_cfg[k] for k in SEARCH},
        },
        "history_top": sorted(history, key=lambda x: -x["score"])[:8],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(out / "improve_loop_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # vorgeschlagene Config mergen und speichern
    proposed = deepcopy(BASE_CFG)
    proposed.update({k: best_cfg[k] for k in SEARCH})
    proposed["mode"] = "active_income_v4_loop"
    proposed["notes"] = "Aus Improve-Loop auf 3 Fenstern – mit Vorsicht nutzen"
    with open(out / "proposed_config_from_loop.json", "w", encoding="utf-8") as f:
        json.dump(proposed, f, indent=2, ensure_ascii=False)

    # Live-config aktualisieren mit bestem Fund
    live = deepcopy(proposed)
    with open(ROOT / "config.json", "w", encoding="utf-8") as f:
        json.dump(live, f, indent=2, ensure_ascii=False)

    print("\n=== BEST ===")
    print(json.dumps(result["best"], indent=2, ensure_ascii=False))
    print("→ improve_loop_result.json + config.json aktualisiert")


if __name__ == "__main__":
    main()
