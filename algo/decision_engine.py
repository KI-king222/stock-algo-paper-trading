"""Active Income: Teil-TP, Monats-Skim, Memory – kein Buy&Hold."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from portfolio import Portfolio

try:
    from algo.memory import AlgoMemory
except ImportError:
    AlgoMemory = None  # type: ignore


@dataclass
class Decision:
    ticker: str
    action: str
    score: float
    reason: str
    shares: float = 0.0


def decide(
    pf: Portfolio,
    prices: Dict[str, float],
    scores: Dict[str, float],
    score_details: Dict[str, Dict[str, float]],
    cfg: dict,
    now: datetime | None = None,
    memory=None,
    force_monthly_skim: bool = False,
) -> List[Decision]:
    now = now or datetime.now()
    tp = float(cfg.get("take_profit_pct", 6.0))
    partial = float(cfg.get("partial_tp_fraction", 0.5))
    dip_tr = float(cfg.get("dip_trigger_pct", -10.0))
    max_dips = int(cfg.get("max_dip_buys_per_ticker", 2))
    min_dip_score = float(cfg.get("min_score_for_dip", -20))
    min_new = float(cfg.get("min_score_for_new_buy", 12))
    min_hold = float(cfg.get("min_score_hold_thesis", -35))
    max_pos_pct = float(cfg.get("max_position_pct_of_equity", 40.0))
    dip_frac = float(cfg.get("dip_cash_fraction", 0.30))
    reinvest_frac = float(cfg.get("reinvest_cash_fraction", 0.55))
    max_new = int(cfg.get("max_new_buys_per_month", 2))
    skim_pct = float(cfg.get("min_monthly_skim_pct", 4.0))
    skim_frac = float(cfg.get("monthly_skim_fraction", 0.35))
    fee = pf.fee_per_trade

    pf.update_prices(prices)
    equity = max(pf.total_equity(), 1.0)
    decisions: List[Decision] = []

    # 1) Take-Profit / Teilverkauf
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        sc = scores.get(t, 0.0)
        pct = pos.unrealized_pnl_pct
        if pct >= tp:
            n = max(1.0, round(pos.shares * partial))
            n = min(n, pos.shares)
            decisions.append(
                Decision(
                    t,
                    "take_profit",
                    sc,
                    f"Teil-TP {pct:.1f}% >= {tp}% → {partial*100:.0f}% der Position",
                    shares=float(n),
                )
            )

    # 2) Monatlicher Skim: auch unter vollem TP, ab min_monthly_skim_pct
    if force_monthly_skim:
        for t, pos in list(pf.positions.items()):
            if t not in prices:
                continue
            if any(d.ticker == t and d.action == "take_profit" for d in decisions):
                continue
            pct = pos.unrealized_pnl_pct
            sc = scores.get(t, 0.0)
            if pct >= skim_pct:
                n = max(1.0, round(pos.shares * skim_frac))
                n = min(n, pos.shares)
                decisions.append(
                    Decision(
                        t,
                        "take_profit",
                        sc,
                        f"Monats-Skim {pct:.1f}% >= {skim_pct}% → {skim_frac*100:.0f}%",
                        shares=float(n),
                    )
                )

    # 3) Dip-Buy mit Memory-Anpassung
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        if any(d.ticker == t and d.action == "take_profit" for d in decisions):
            continue
        sc = scores.get(t, 0.0)
        pct = pos.unrealized_pnl_pct
        adj = 0.0
        if memory is not None and cfg.get("memory_enabled", True):
            adj = memory.dip_score_adjust(t)
        need = min_dip_score - adj

        if pct <= dip_tr and pos.dip_buy_count < max_dips and sc >= need:
            budget = pf.cash * dip_frac
            max_mv = equity * (max_pos_pct / 100.0)
            room = max_mv - pos.market_value
            spend = min(budget, room) if room > 0 else 0
            n = int((spend - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(
                        t,
                        "dip_buy",
                        sc,
                        f"Dip {pct:.1f}% score {sc:.0f} (mem_adj {adj:+.1f})",
                        shares=float(n),
                    )
                )
            else:
                decisions.append(Decision(t, "hold", sc, "Dip-Zone ohne Budget"))
        elif sc < min_hold:
            decisions.append(
                Decision(t, "hold", sc, f"Score schwach {sc:.0f} – halten, kein Sell im Minus")
            )
        else:
            decisions.append(
                Decision(t, "hold", sc, f"P&L {pct:.1f}% score {sc:.0f}")
            )

    # 4) Cash: Reinvest + Neukauf (aktiv, nicht parken)
    if cfg.get("reinvest_idle_cash", True) and pf.cash > fee * 2 + 120:
        open_ranked = []
        for t, pos in pf.positions.items():
            if t not in prices:
                continue
            if any(d.ticker == t and d.action in ("take_profit", "dip_buy") for d in decisions):
                continue
            if pos.unrealized_pnl_pct >= tp - 1:
                continue
            sc = scores.get(t, 0.0)
            pref = memory.prefer_ticker(t) if memory else 0.0
            if sc + pref * 5 >= 0:
                open_ranked.append((sc + pref * 5, t))
        open_ranked.sort(reverse=True)
        if open_ranked:
            sc, t = open_ranked[0]
            budget = pf.cash * reinvest_frac
            max_mv = equity * (max_pos_pct / 100.0)
            room = max_mv - pf.positions[t].market_value
            spend = min(budget, room) if room > 0 else 0
            n = int((spend - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(t, "reinvest", sc, f"Reinvest score {sc:.0f}", shares=float(n))
                )

        held = set(pf.positions.keys())
        cands = []
        for t, sc in scores.items():
            if t in held or t not in prices:
                continue
            pref = memory.prefer_ticker(t) if memory else 0.0
            if sc + pref * 3 >= min_new:
                cands.append((sc + pref * 3, t, sc))
        cands.sort(reverse=True)
        for _, t, sc in cands[:max_new]:
            budget = pf.cash * 0.28
            n = int((budget - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(t, "new_buy", sc, f"Neukauf score {sc:.0f}", shares=float(n))
                )

    return decisions


def apply_decisions(
    pf: Portfolio,
    decisions: List[Decision],
    prices: Dict[str, float],
    now: datetime,
    memory=None,
) -> List[dict]:
    log = []
    for d in decisions:
        if d.action == "take_profit" and d.ticker in pf.positions and d.ticker in prices:
            ok = pf.sell(
                d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo TP: {d.reason}"
            )
            if ok and memory is not None:
                memory.record_tp(d.ticker)
            log.append(
                {
                    "ticker": d.ticker,
                    "action": "sell_tp",
                    "ok": ok,
                    "shares": d.shares,
                    "reason": d.reason,
                    "score": d.score,
                }
            )
        elif d.action in ("dip_buy", "reinvest", "new_buy") and d.shares > 0 and d.ticker in prices:
            is_dip = d.action == "dip_buy"
            ok = pf.buy(
                d.ticker,
                d.shares,
                prices[d.ticker],
                now,
                reason=f"Algo {d.action}: {d.reason}",
                is_dip=is_dip,
            )
            if ok and is_dip and memory is not None:
                memory.record_dip(d.ticker)
            log.append(
                {
                    "ticker": d.ticker,
                    "action": d.action,
                    "ok": ok,
                    "shares": d.shares,
                    "reason": d.reason,
                    "score": d.score,
                }
            )
        else:
            log.append(
                {
                    "ticker": d.ticker,
                    "action": d.action,
                    "ok": True,
                    "reason": d.reason,
                    "score": d.score,
                }
            )
    return log
