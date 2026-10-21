#!/usr/bin/env python3
"""Täglicher/manueller Algo-Lauf: Fundamentals → Score → Regeln → State."""
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
from algo.decision_engine import apply_decisions, decide  # noqa: E402

STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "portfolio_state.json"
LOG_FILE = STATE_DIR / "decisions_log.json"
CFG_FILE = ROOT / "config.json"


def load_cfg() -> dict:
    with open(CFG_FILE, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg = load_cfg()
    universe = list(cfg["universe"])
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    pf = Portfolio.load(STATE_FILE)
    if pf is None:
        pf = Portfolio(
            starting_cash=float(cfg.get("starting_cash", 10_000)),
            fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
        )
        # Initial equal-weight
        prices0 = get_prices(universe)
        per = pf.cash / max(len(prices0), 1)
        now = datetime.now()
        for t, p in prices0.items():
            n = int((per - pf.fee_per_trade) // p)
            if n > 0:
                pf.buy(t, float(n), p, now, reason="Algo: Initial equal-weight")

    tickers = list(set(universe) | set(pf.positions.keys()))
    prices = get_prices(tickers)
    scores = {}
    details = {}
    for t in tickers:
        fund = get_fundamentals(t)
        sc, parts = score_fundamentals(fund)
        scores[t] = sc
        details[t] = parts
        print(f"  Score {t}: {sc:+.1f}  {parts}")

    now = datetime.now()
    decisions = decide(pf, prices, scores, details, cfg, now)
    log_entries = apply_decisions(pf, decisions, prices, now)
    pf.update_prices(prices)
    pf.save(STATE_FILE)

    run = {
        "date": now.isoformat(timespec="seconds"),
        "source": f"Algo: {now.strftime('%d.%m.%Y %H:%M')} – algorithmischer Lauf",
        "scores": scores,
        "score_details": details,
        "decisions": log_entries,
        "cash": pf.cash,
        "equity": pf.total_equity(),
        "external_profit": pf.external_profit,
        "wealth": pf.total_wealth(),
    }

    history = []
    if LOG_FILE.exists():
        with open(LOG_FILE, encoding="utf-8") as f:
            history = json.load(f).get("runs", [])
    history.append(run)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"runs": history[-200:]}, f, indent=2, ensure_ascii=False)

    print("=" * 50)
    print(f"Equity: {pf.total_equity():,.2f} € | Wealth: {pf.total_wealth():,.2f} € | Cash: {pf.cash:,.2f} €")
    print(f"Decisions: {len(log_entries)}")
    for e in log_entries:
        print(f"  {e}")
    print(f"State → {STATE_FILE}")


if __name__ == "__main__":
    main()
