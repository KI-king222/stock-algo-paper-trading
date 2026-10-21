#!/usr/bin/env python3
"""Täglicher Algo-Lauf: Finanzbericht-Trends + Score v2 + Gehirn."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import get_financial_report, get_fundamentals, get_prices  # noqa: E402
from portfolio import Portfolio  # noqa: E402
from algo.scoring import score_combined  # noqa: E402
from algo.decision_engine import apply_decisions, decide  # noqa: E402
from algo.memory import AlgoMemory  # noqa: E402

STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "portfolio_state.json"
LOG_FILE = STATE_DIR / "decisions_log.json"
MEMORY_FILE = STATE_DIR / "algo_memory.json"
STATUS_FILE = STATE_DIR / "last_run_status.json"
REPORTS_FILE = STATE_DIR / "last_reports.json"
CFG_FILE = ROOT / "config.json"


def load_cfg() -> dict:
    with open(CFG_FILE, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg = load_cfg()
    universe = list(cfg["universe"])
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    mem = AlgoMemory.from_dict(
        json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if MEMORY_FILE.exists()
        else None
    ) if cfg.get("memory_enabled", True) else None

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

    scores, details, report_texts, reports_out = {}, {}, {}, {}
    print("=== Finanzbericht- / Kennzahlen-Analyse ===")
    for t in tickers:
        fund = get_fundamentals(t)
        report = get_financial_report(t)
        sc, parts, rtext = score_combined(fund, report)
        scores[t] = sc
        details[t] = parts
        report_texts[t] = rtext
        reports_out[t] = {
            "findings": report.get("findings"),
            "data_quality": report.get("data_quality"),
            "revenue_growth_yoy": report.get("revenue_growth_yoy"),
            "earnings_growth_yoy": report.get("earnings_growth_yoy"),
            "score": round(sc, 1),
            "lessons": mem.lessons_for(t) if mem else "",
        }
        print(f"\n{t} Score {sc:+.1f}")
        for line in report.get("findings") or []:
            print(f"  · {line}")
        if mem:
            les = mem.lessons_for(t)
            if les:
                print(f"  Gehirn: {les}")

    now = datetime.now()
    force_skim = now.day <= 3
    decisions = decide(
        pf, prices, scores, details, cfg, now, memory=mem, force_monthly_skim=force_skim
    )
    log_entries = apply_decisions(pf, decisions, prices, now, memory=mem)

    # Neue Episoden für aktive Entscheidungen
    if mem is not None:
        for e in log_entries:
            akt = e.get("aktion") or e.get("action") or ""
            if akt in ("halten", "hold") and not e.get("stück"):
                continue
            t = e.get("ticker")
            if not t:
                continue
            mem.add_episode(
                ticker=t,
                action=akt,
                score=float(e.get("score") or scores.get(t) or 0),
                price=float(prices.get(t) or 0),
                reason=str(e.get("grund") or e.get("reason") or ""),
                report_summary=report_texts.get(t, ""),
                outcome="offen",
                lesson="",
            )
        # Offene Dips bewerten
        for t, pos in pf.positions.items():
            if pos.dip_buy_count > 0:
                mem.record_dip_underwater(t, pos.unrealized_pnl_pct < -5)

    pf.update_prices(prices)
    pf.save(STATE_FILE)
    if mem is not None:
        MEMORY_FILE.write_text(
            json.dumps(mem.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    REPORTS_FILE.write_text(
        json.dumps(
            {"date": now.isoformat(timespec="seconds"), "reports": reports_out},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    scores_r = {k: round(float(v), 1) for k, v in scores.items()}
    run = {
        "date": now.isoformat(timespec="seconds"),
        "source": f"Algo: {now.strftime('%d.%m.%Y %H:%M')} – Bericht+Score+Gehirn",
        "scores": scores_r,
        "score_details": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in details.items()},
        "report_summaries": report_texts,
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
            f"Lohn {pf.external_profit:,.0f} € | Rest-Cash {pf.cash:,.0f} €"
        ),
        "cash": round(pf.cash, 2),
        "equity": round(pf.total_equity(), 2),
        "external_profit": round(pf.external_profit, 2),
        "wealth": round(pf.total_wealth(), 2),
        "hinweis": "Rest-Cash ≠ Gewinn. Lohn erst nach Verkauf im Plus. Analyse = Kennzahlen + Quartalstrends.",
        "n_decisions": len(log_entries),
        "actions_summary": [
            f"{e.get('aktion')} {e.get('ticker', '')}" for e in log_entries if e.get("ok")
        ][:12],
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 50)
    print(
        f"Vermögen: {pf.total_wealth():,.2f} € | Lohn: {pf.external_profit:,.2f} € | "
        f"Rest-Cash: {pf.cash:,.2f} €"
    )
    for e in log_entries:
        print(f"  {e}")


if __name__ == "__main__":
    main()
