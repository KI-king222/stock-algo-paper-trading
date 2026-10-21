#!/usr/bin/env python3
"""Täglicher Algo-Lauf."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import get_fundamentals, get_prices  # noqa: E402
from portfolio import Portfolio  # noqa: E402
from algo.scoring import score_fundamentals  # noqa: E402
from algo.decision_engine import ACTION_DE, apply_decisions, decide  # noqa: E402
from algo.memory import AlgoMemory  # noqa: E402

STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "portfolio_state.json"
LOG_FILE = STATE_DIR / "decisions_log.json"
MEMORY_FILE = STATE_DIR / "algo_memory.json"
STATUS_FILE = STATE_DIR / "last_run_status.json"
CFG_FILE = ROOT / "config.json"


def load_cfg() -> dict:
    with open(CFG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_memory() -> AlgoMemory:
    mem = AlgoMemory()
    if MEMORY_FILE.exists():
        try:
            d = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            for k, v in (d.get("tp_success") or {}).items():
                mem.tp_success[k] = int(v)
            for k, v in (d.get("dip_success") or {}).items():
                mem.dip_success[k] = int(v)
            for k, v in (d.get("dip_fail") or {}).items():
                mem.dip_fail[k] = int(v)
            for k, v in (d.get("bias") or {}).items():
                mem.bias[k] = float(v)
        except Exception as e:
            print(f"Memory: {e}")
    return mem


def save_memory(mem: AlgoMemory):
    MEMORY_FILE.write_text(
        json.dumps(mem.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    cfg = load_cfg()
    universe = list(cfg["universe"])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    mem = load_memory() if cfg.get("memory_enabled", True) else None

    pf = Portfolio.load(STATE_FILE)
    if pf is None:
        pf = Portfolio(
            starting_cash=float(cfg.get("starting_cash", 10_000)),
            fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
        )
        prices0 = get_prices(universe)
        per = pf.cash / max(len(prices0), 1)
        now = datetime.now()
        for t, p in prices0.items():
            n = int((per - pf.fee_per_trade) // p)
            if n > 0:
                pf.buy(t, float(n), p, now, reason="Algo: Erste Aufteilung")

    tickers = list(set(universe) | set(pf.positions.keys()))
    prices = get_prices(tickers)
    scores, details = {}, {}
    for t in tickers:
        fund = get_fundamentals(t)
        sc, parts = score_fundamentals(fund)
        scores[t] = sc
        details[t] = parts
        print(f"  Score {t}: {sc:+.1f}")

    now = datetime.now()
    force_skim = now.day <= 3
    decisions = decide(
        pf, prices, scores, details, cfg, now, memory=mem, force_monthly_skim=force_skim
    )
    log_entries = apply_decisions(pf, decisions, prices, now, memory=mem)
    pf.update_prices(prices)
    pf.save(STATE_FILE)
    if mem is not None:
        save_memory(mem)

    # Scores für Anzeige runden
    scores_r = {k: round(float(v), 1) for k, v in scores.items()}

    run = {
        "date": now.isoformat(timespec="seconds"),
        "source": f"Algo: {now.strftime('%d.%m.%Y %H:%M')} – algorithmischer Lauf",
        "scores": scores_r,
        "score_details": details,
        "decisions": log_entries,
        "cash": round(pf.cash, 2),
        "equity": round(pf.total_equity(), 2),
        "external_profit": round(pf.external_profit, 2),
        "wealth": round(pf.total_wealth(), 2),
    }

    history = []
    if LOG_FILE.exists():
        with open(LOG_FILE, encoding="utf-8") as f:
            history = json.load(f).get("runs", [])
    history.append(run)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"runs": history[-200:]}, f, indent=2, ensure_ascii=False)

    status = {
        "last_run": now.isoformat(timespec="seconds"),
        "headline": (
            f"Algo {now.strftime('%d.%m.%Y %H:%M')} | "
            f"Vermögen {pf.total_wealth():,.0f} € | "
            f"Lohn (realisiert) {pf.external_profit:,.0f} € | "
            f"Rest-Cash {pf.cash:,.0f} €"
        ),
        "cash": round(pf.cash, 2),
        "equity": round(pf.total_equity(), 2),
        "external_profit": round(pf.external_profit, 2),
        "wealth": round(pf.total_wealth(), 2),
        "hinweis": (
            "Rest-Cash ist kein Gewinn. Realisierter Lohn = external_profit "
            "(erst nach Verkauf im Plus)."
        ),
        "n_decisions": len(log_entries),
        "actions_summary": [
            f"{e.get('aktion')} {e.get('ticker', '')}" for e in log_entries if e.get("ok")
        ][:12],
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 50)
    print(
        f"Vermögen: {pf.total_wealth():,.2f} € | Equity: {pf.total_equity():,.2f} € | "
        f"Lohn: {pf.external_profit:,.2f} € | Rest-Cash: {pf.cash:,.2f} €"
    )
    for e in log_entries:
        print(f"  {e}")


if __name__ == "__main__":
    main()
