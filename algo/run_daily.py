#!/usr/bin/env python3
"""Täglicher Algo-Lauf v4."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from data import get_financial_report, get_fundamentals, get_prices  # noqa: E402
from portfolio import Portfolio  # noqa: E402
from algo.scoring import score_combined  # noqa: E402
from algo.decision_engine import RunContext, apply_decisions, decide  # noqa: E402
from algo.memory import AlgoMemory  # noqa: E402

STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "portfolio_state.json"
LOG_FILE = STATE_DIR / "decisions_log.json"
MEMORY_FILE = STATE_DIR / "algo_memory.json"
STATUS_FILE = STATE_DIR / "last_run_status.json"
REPORTS_FILE = STATE_DIR / "last_reports.json"
CTX_FILE = STATE_DIR / "run_context.json"
CFG_FILE = ROOT / "config.json"


def load_cfg() -> dict:
    with open(CFG_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_ctx() -> RunContext:
    ctx = RunContext(cooldown_days=5)
    if CTX_FILE.exists():
        try:
            d = json.loads(CTX_FILE.read_text(encoding="utf-8"))
            for t, s in (d.get("recently_sold") or {}).items():
                ctx.recently_sold[t] = datetime.fromisoformat(s)
            for t, p in (d.get("peaks") or {}).items():
                ctx.peaks[t] = float(p)
        except Exception:
            pass
    return ctx


def save_ctx(ctx: RunContext):
    CTX_FILE.write_text(
        json.dumps(
            {
                "recently_sold": {k: v.isoformat() for k, v in ctx.recently_sold.items()},
                "peaks": ctx.peaks,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def price_momentum(ticker: str) -> dict:
    try:
        h = yf.Ticker(ticker).history(period="1mo", auto_adjust=True)
        if h is None or h.empty or len(h) < 3:
            return {"m5": None, "m10": None}
        s = h["Close"].dropna()
        last = float(s.iloc[-1])
        m5 = (last / float(s.iloc[-5]) - 1) * 100 if len(s) >= 5 else None
        m10 = (last / float(s.iloc[-10]) - 1) * 100 if len(s) >= 10 else None
        return {"m5": m5, "m10": m10}
    except Exception:
        return {"m5": None, "m10": None}


def main() -> None:
    cfg = load_cfg()
    universe = list(cfg["universe"])
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    mem = (
        AlgoMemory.from_dict(
            json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            if MEMORY_FILE.exists()
            else None
        )
        if cfg.get("memory_enabled", True)
        else None
    )
    ctx = load_ctx()

    pf = Portfolio.load(STATE_FILE)
    if pf is None:
        pf = Portfolio(
            starting_cash=float(cfg.get("starting_cash", 10_000)),
            fee_per_trade=float(cfg.get("fee_per_trade", 1.0)),
        )
        prices0 = get_prices(universe)
        now = datetime.now()
        # Score-gewichteter Start
        weights = {}
        for t in prices0:
            fund = get_fundamentals(t)
            report = get_financial_report(t)
            sc, _, _ = score_combined(fund, report, price_momentum(t))
            weights[t] = max(sc, 5.0)
        total_w = sum(weights.values()) or 1.0
        for t, p in prices0.items():
            budget = pf.cash * (weights[t] / total_w) * 0.95
            n = int((budget - pf.fee_per_trade) // p)
            if n > 0:
                pf.buy(t, float(n), p, now, reason=f"Algo: Start Score-Gewicht ({weights[t]:.0f})")
                ctx.update_peak(t, p)

    tickers = list(set(universe) | set(pf.positions.keys()))
    prices = get_prices(tickers)

    scores, details, report_texts, reports_out = {}, {}, {}, {}
    print("=== Analyse v4 ===")
    for t in tickers:
        fund = get_fundamentals(t)
        report = get_financial_report(t)
        mom = price_momentum(t)
        sc, parts, rtext = score_combined(fund, report, momentum=mom)
        scores[t] = sc
        details[t] = parts
        report_texts[t] = rtext
        reports_out[t] = {
            "findings": report.get("findings"),
            "momentum": mom,
            "score": round(sc, 1),
            "lessons": mem.lessons_for(t) if mem else "",
        }
        print(f"\n{t} Score {sc:+.1f} mom={mom}")

    now = datetime.now()
    force_skim = now.day <= 3
    decisions = decide(pf, prices, scores, details, cfg, now, mem, force_skim, ctx)
    log_entries = apply_decisions(pf, decisions, prices, now, mem, ctx)

    if mem is not None:
        for e in log_entries:
            akt = e.get("aktion") or ""
            if akt in ("halten",) and not e.get("stück"):
                continue
            t = e.get("ticker")
            if not t:
                continue
            mem.add_episode(
                ticker=t,
                action=akt,
                score=float(e.get("score") or scores.get(t) or 0),
                price=float(prices.get(t) or 0),
                reason=str(e.get("grund") or ""),
                report_summary=report_texts.get(t, ""),
                outcome="offen",
                lesson="",
            )

    pf.update_prices(prices)
    pf.save(STATE_FILE)
    save_ctx(ctx)
    if mem is not None:
        MEMORY_FILE.write_text(
            json.dumps(mem.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    REPORTS_FILE.write_text(
        json.dumps({"date": now.isoformat(timespec="seconds"), "reports": reports_out}, indent=2),
        encoding="utf-8",
    )

    run = {
        "date": now.isoformat(timespec="seconds"),
        "source": f"Algo: {now.strftime('%d.%m.%Y %H:%M')} – v4 Regime+Trailing+TP4.5",
        "scores": {k: round(float(v), 1) for k, v in scores.items()},
        "score_details": details,
        "report_summaries": report_texts,
        "decisions": log_entries,
        "cash": round(pf.cash, 2),
        "equity": round(pf.total_equity(), 2),
        "external_profit": round(pf.external_profit, 2),
        "wealth": round(pf.total_wealth(), 2),
    }
    history = []
    if LOG_FILE.exists():
        history = json.loads(LOG_FILE.read_text(encoding="utf-8")).get("runs", [])
    history.append(run)
    LOG_FILE.write_text(
        json.dumps({"runs": history[-200:]}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    status = {
        "last_run": now.isoformat(timespec="seconds"),
        "headline": (
            f"Algo v4 {now.strftime('%d.%m.%Y %H:%M')} | "
            f"Vermögen {pf.total_wealth():,.0f} € | Lohn {pf.external_profit:,.0f} €"
        ),
        "cash": round(pf.cash, 2),
        "equity": round(pf.total_equity(), 2),
        "external_profit": round(pf.external_profit, 2),
        "wealth": round(pf.total_wealth(), 2),
        "hinweis": "v4: TP ab ~4,5%, Trailing, kein Kauf bei schwachem Markt-Momentum.",
        "n_decisions": len(log_entries),
        "actions_summary": [f"{e.get('aktion')} {e.get('ticker', '')}" for e in log_entries][:12],
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    print("=" * 50)
    print(f"Vermögen {pf.total_wealth():,.2f} € | Lohn {pf.external_profit:,.2f} €")
    for e in log_entries:
        print(f"  {e}")


if __name__ == "__main__":
    main()
