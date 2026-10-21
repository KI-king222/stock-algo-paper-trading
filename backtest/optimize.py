#!/usr/bin/env python3
"""
Wiederholte 5y-Backtests mit Parameter-Suche, bis Ziel-Vermögen erreicht
oder max. Versuche ausgeschöpft sind.

WARNUNG – Overfitting:
  Das optimiert auf EINEM historischen Pfad (2021–2026, 4 Ticker).
  Parameter, die hier 20.000 € schaffen, sind KEIN Garant für die Zukunft.
  Ergebnis = „hätte auf dieser Vergangenheit funktioniert“, nicht „wird wieder so".
"""
from __future__ import annotations

import copy
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.run_backtest import run  # noqa: E402

TARGET_WEALTH = 20_000.0
MAX_TRIALS = 40
START = "2021-10-20"
END = "2026-10-20"

# Suchraum (diskrete sinnvolle Werte)
SEARCH_SPACE = {
    "take_profit_pct": [8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 50.0, 100.0],
    "dip_trigger_pct": [-8.0, -10.0, -12.0, -15.0, -20.0],
    "max_dip_buys_per_ticker": [0, 1, 2, 3],
    "dip_cash_fraction": [0.20, 0.30, 0.35, 0.45],
    "min_score_for_dip": [-40, -25, -15, -5, 0],
    "min_score_for_new_buy": [10, 15, 25, 40, 999],  # 999 ≈ kein Neukauf
    "reinvest_cash_fraction": [0.0, 0.30, 0.50, 0.70],
    "max_new_buys_per_month": [0, 1, 2],
    "max_position_pct_of_equity": [35.0, 45.0, 55.0, 70.0],
}


def load_base_cfg() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def sample_params(rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in SEARCH_SPACE.items()}


def apply_params(base: dict, params: dict) -> dict:
    cfg = copy.deepcopy(base)
    cfg.update(params)
    # Flag: Reinvest aus wenn Fraction 0
    cfg["reinvest_idle_cash"] = float(params.get("reinvest_cash_fraction", 0)) > 0.01
    return cfg


def run_with_cfg(cfg: dict) -> dict:
    """Monkeypatch load_cfg in run_backtest via config file write temporarily."""
    cfg_path = ROOT / "config.json"
    backup = cfg_path.read_text(encoding="utf-8")
    try:
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        result, _pf = run(START, END)
        return result
    finally:
        cfg_path.write_text(backup, encoding="utf-8")


def main():
    rng = random.Random(42)
    base = load_base_cfg()
    out_dir = ROOT / "backtest" / "results" / "optimize"
    out_dir.mkdir(parents=True, exist_ok=True)

    trials: List[dict] = []
    best: Dict[str, Any] = {"wealth": -1.0}

    # Trial 0 = aktuelle Config
    print("Trial 0 = aktuelle config.json")
    r0 = run(START, END)[0]
    w0 = float(r0["strategy"]["total_wealth"])
    trial0 = {
        "trial": 0,
        "params": {k: base.get(k) for k in SEARCH_SPACE},
        "wealth": w0,
        "return_pct": r0["strategy"]["return_wealth_pct"],
        "max_drawdown_pct": r0["max_drawdown_pct"],
        "trades": r0["strategy"]["trades"],
    }
    trials.append(trial0)
    best = {**trial0, "full": r0}
    print(f"  → {w0:.2f} €")

    if w0 >= TARGET_WEALTH:
        print(f"Ziel {TARGET_WEALTH:.0f} € bereits mit aktueller Config erreicht.")
    else:
        for i in range(1, MAX_TRIALS + 1):
            params = sample_params(rng)
            # 30% Chance: um besten herum mutieren
            if best["wealth"] > 0 and rng.random() < 0.35:
                params = {**best["params"]}
                key = rng.choice(list(SEARCH_SPACE.keys()))
                params[key] = rng.choice(SEARCH_SPACE[key])

            cfg = apply_params(base, params)
            print(f"Trial {i}/{MAX_TRIALS} params={params}")
            try:
                result = run_with_cfg(cfg)
            except Exception as e:
                print(f"  FEHLER: {e}")
                trials.append({"trial": i, "params": params, "error": str(e)})
                continue

            wealth = float(result["strategy"]["total_wealth"])
            row = {
                "trial": i,
                "params": params,
                "wealth": wealth,
                "return_pct": result["strategy"]["return_wealth_pct"],
                "max_drawdown_pct": result["max_drawdown_pct"],
                "trades": result["strategy"]["trades"],
                "tp": result["strategy"].get("take_profit_events"),
                "dip": result["strategy"].get("dip_buy_events"),
            }
            trials.append(row)
            print(
                f"  → {wealth:.2f} € | DD {result['max_drawdown_pct']}% | "
                f"Trades {row['trades']}"
            )

            if wealth > best["wealth"]:
                best = {**row, "full": result}
                print(f"  ★ neues Best")

            if wealth >= TARGET_WEALTH:
                print(f"\nZiel {TARGET_WEALTH:.0f} € erreicht in Trial {i}.")
                break
        else:
            print(
                f"\nZiel {TARGET_WEALTH:.0f} € nach {MAX_TRIALS} Trials nicht erreicht. "
                f"Best: {best['wealth']:.2f} €"
            )

    summary = {
        "target_wealth": TARGET_WEALTH,
        "max_trials": MAX_TRIALS,
        "achieved": best["wealth"] >= TARGET_WEALTH,
        "best_wealth": best["wealth"],
        "best_params": best.get("params"),
        "best_drawdown_pct": best.get("max_drawdown_pct"),
        "best_trades": best.get("trades"),
        "trials_run": len([t for t in trials if "wealth" in t]),
        "warning": (
            "Overfitting-Risiko: Optimierung auf einem historischen Pfad. "
            "Nicht als Zukunftsgarantie verwenden."
        ),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "all_trials": trials,
    }

    # Best-Config speichern (nicht live config überschreiben ohne Flag)
    best_cfg = apply_params(base, best.get("params") or {})
    with open(out_dir / "best_config.json", "w", encoding="utf-8") as f:
        json.dump(best_cfg, f, indent=2, ensure_ascii=False)
    with open(out_dir / "optimize_summary.json", "w", encoding="utf-8") as f:
        # full result kann groß sein – ohne equity details
        dump = {k: v for k, v in summary.items()}
        if "full" in best:
            dump["best_strategy"] = best["full"].get("strategy")
            dump["best_benchmark"] = best["full"].get("benchmark_buy_hold")
        json.dump(dump, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print(f"Best Vermögen: {best['wealth']:.2f} €")
    print(f"Params: {best.get('params')}")
    print(f"→ {out_dir / 'optimize_summary.json'}")


if __name__ == "__main__":
    main()
