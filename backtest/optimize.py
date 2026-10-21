#!/usr/bin/env python3
"""
Optimize Active-Income: maximiert income_score
(= Vermögen + Bonus für realisierte Monats-Löhne).
Kein TP=100 Buy&Hold im Suchraum.
"""
from __future__ import annotations

import copy
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.run_backtest import run  # noqa: E402

TARGET_WEALTH = 18_000.0  # ambitioniert aber ohne BH-Zwang
MIN_PAYOUT_MONTHS = 15  # mindestens so viele Monate mit Lohn
MAX_TRIALS = 45
START = "2021-10-20"
END = "2026-10-20"

# Bewusst KEIN 50/100% TP – das wäre wieder Buy&Hold
SEARCH_SPACE = {
    "take_profit_pct": [4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0],
    "partial_tp_fraction": [0.3, 0.4, 0.5, 0.6, 0.75],
    "dip_trigger_pct": [-8.0, -10.0, -12.0, -15.0],
    "max_dip_buys_per_ticker": [1, 2, 3],
    "dip_cash_fraction": [0.25, 0.30, 0.40],
    "min_score_for_dip": [-30, -20, -10],
    "min_score_for_new_buy": [8, 12, 20, 30],
    "reinvest_cash_fraction": [0.40, 0.55, 0.70],
    "max_new_buys_per_month": [1, 2],
    "min_monthly_skim_pct": [3.0, 4.0, 5.0, 6.0],
    "monthly_skim_fraction": [0.25, 0.35, 0.45],
    "max_position_pct_of_equity": [35.0, 40.0, 50.0],
}


def load_base_cfg() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def sample_params(rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in SEARCH_SPACE.items()}


def apply_params(base: dict, params: dict) -> dict:
    cfg = copy.deepcopy(base)
    cfg.update(params)
    cfg["mode"] = "active_income"
    cfg["weekly_tp_check"] = True
    cfg["memory_enabled"] = True
    cfg["reinvest_idle_cash"] = True
    return cfg


def score_of(result: dict) -> float:
    return float(result["strategy"].get("income_score") or result["strategy"]["total_wealth"])


def main():
    rng = random.Random(7)
    base = load_base_cfg()
    out_dir = ROOT / "backtest" / "results" / "optimize"
    out_dir.mkdir(parents=True, exist_ok=True)

    trials: List[dict] = []
    best: Dict[str, Any] = {"score": -1.0, "wealth": -1.0}

    print(f"Active-Income Optimize | Ziel Wealth>={TARGET_WEALTH} & payout_months>={MIN_PAYOUT_MONTHS}")

    for i in range(0, MAX_TRIALS + 1):
        if i == 0:
            params = {k: base.get(k) for k in SEARCH_SPACE}
            cfg = apply_params(base, {k: base.get(k) for k in SEARCH_SPACE if k in base})
        else:
            params = sample_params(rng)
            if best.get("score", -1) > 0 and rng.random() < 0.45:
                params = {**best["params"]}
                key = rng.choice(list(SEARCH_SPACE.keys()))
                params[key] = rng.choice(SEARCH_SPACE[key])
            cfg = apply_params(base, params)

        print(f"Trial {i}/{MAX_TRIALS}")
        try:
            result, _ = run(START, END, cfg=cfg)
        except Exception as e:
            print(f"  ERR {e}")
            trials.append({"trial": i, "params": params, "error": str(e)})
            continue

        s = result["strategy"]
        sc = score_of(result)
        row = {
            "trial": i,
            "params": params,
            "score": sc,
            "wealth": s["total_wealth"],
            "external_profit": s["external_profit"],
            "payout_sum": s["total_realized_payouts"],
            "months_with_payout": s["months_with_payout"],
            "avg_payout": s["avg_payout_per_paying_month"],
            "max_drawdown_pct": result["max_drawdown_pct"],
            "trades": s["trades"],
            "tp_events": s["take_profit_events"],
        }
        trials.append(row)
        print(
            f"  score {sc:.0f} | wealth {s['total_wealth']:.0f} | "
            f"Lohn {s['total_realized_payouts']:.0f} in {s['months_with_payout']} Mon | "
            f"DD {result['max_drawdown_pct']}%"
        )

        if sc > best.get("score", -1):
            best = {**row, "full": result}
            print("  ★ Best")

        # Ziel: genug Wealth UND genug Monate mit Lohn (anti-BuyHold)
        if (
            s["total_wealth"] >= TARGET_WEALTH
            and s["months_with_payout"] >= MIN_PAYOUT_MONTHS
        ):
            print("Zielkombination erreicht.")
            break

    summary = {
        "objective": "max income_score (wealth + payout bonus + months bonus)",
        "target_wealth": TARGET_WEALTH,
        "min_payout_months": MIN_PAYOUT_MONTHS,
        "achieved_combo": (
            best.get("wealth", 0) >= TARGET_WEALTH
            and best.get("months_with_payout", 0) >= MIN_PAYOUT_MONTHS
        ),
        "best": {k: v for k, v in best.items() if k != "full"},
        "best_monthly_payouts": (best.get("full") or {})
        .get("strategy", {})
        .get("monthly_payouts", {}),
        "warning": "Historische Optimierung ≠ Zukunft. Active mode, kein TP100.",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "trials": trials,
    }
    best_cfg = apply_params(base, best.get("params") or {})
    with open(out_dir / "best_config.json", "w", encoding="utf-8") as f:
        json.dump(best_cfg, f, indent=2, ensure_ascii=False)
    with open(out_dir / "optimize_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    # Live-Config auf Best setzen (Active Income)
    with open(ROOT / "config.json", "w", encoding="utf-8") as f:
        json.dump(best_cfg, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print(f"Best score {best.get('score')} wealth {best.get('wealth')} "
          f"Lohn-Monate {best.get('months_with_payout')}")
    print(f"Params {best.get('params')}")


if __name__ == "__main__":
    main()
