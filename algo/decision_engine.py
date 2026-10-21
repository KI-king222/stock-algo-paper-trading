"""Score + Portfolio-Regeln → Aktionen (v2: Dip vor Neukauf, weniger Idle-Cash)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from portfolio import Portfolio


@dataclass
class Decision:
    ticker: str
    action: str  # take_profit | hold | dip_buy | new_buy | reinvest | skip
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
) -> List[Decision]:
    now = now or datetime.now()
    tp = float(cfg.get("take_profit_pct", 12.0))
    dip_tr = float(cfg.get("dip_trigger_pct", -12.0))
    max_dips = int(cfg.get("max_dip_buys_per_ticker", 2))
    min_dip_score = float(cfg.get("min_score_for_dip", -25))
    min_new = float(cfg.get("min_score_for_new_buy", 15))
    min_hold = float(cfg.get("min_score_hold_thesis", -40))
    max_pos_pct = float(cfg.get("max_position_pct_of_equity", 45.0))
    dip_frac = float(cfg.get("dip_cash_fraction", 0.35))
    reinvest_frac = float(cfg.get("reinvest_cash_fraction", 0.50))
    max_new = int(cfg.get("max_new_buys_per_month", 1))
    fee = pf.fee_per_trade

    pf.update_prices(prices)
    equity = max(pf.total_equity(), 1.0)
    decisions: List[Decision] = []

    # 1) Take-Profit – harte Regel, Score irrelevant
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        sc = scores.get(t, 0.0)
        if pos.unrealized_pnl_pct >= tp:
            decisions.append(
                Decision(
                    ticker=t,
                    action="take_profit",
                    score=sc,
                    reason=f"unrealized {pos.unrealized_pnl_pct:.1f}% >= TP {tp}%",
                    shares=pos.shares,
                )
            )

    # 2) Dip-Buy priorisieren (Haupt-Hebel aus Backtest)
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        if any(d.ticker == t and d.action == "take_profit" for d in decisions):
            continue
        sc = scores.get(t, 0.0)
        pct = pos.unrealized_pnl_pct
        details = score_details.get(t) or {}

        if pct <= dip_tr and pos.dip_buy_count < max_dips:
            # Score-Filter nur bei extrem schwach; Momentum-Crash (m60 sehr negativ + hoher DD) blockt
            m60 = float(details.get("m60") or 0)
            dd = float(details.get("dd") or 0)
            broken = sc < min_dip_score or (m60 < -35 and dd < -40)
            if broken:
                decisions.append(
                    Decision(
                        t,
                        "hold",
                        sc,
                        f"Dip-Zone, aber Score/Trend zu schwach (sc={sc:.0f}, m60={m60:.0f}) – kein Nachkauf",
                    )
                )
                continue
            budget = pf.cash * dip_frac
            max_mv = equity * (max_pos_pct / 100.0)
            room = max_mv - pos.market_value
            spend = min(budget, room) if room > 0 else 0
            n = int((spend - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0 and spend > fee + prices[t]:
                decisions.append(
                    Decision(
                        ticker=t,
                        action="dip_buy",
                        score=sc,
                        reason=f"Dip {pct:.1f}% | score {sc:.0f} | dips {pos.dip_buy_count}/{max_dips}",
                        shares=float(n),
                    )
                )
            else:
                decisions.append(
                    Decision(t, "hold", sc, f"Dip-Zone, kein Budget/Cap | score {sc:.0f}")
                )
        elif sc < min_hold:
            decisions.append(
                Decision(
                    t,
                    "hold",
                    sc,
                    f"Score {sc:.0f} schwach – halten (kein Auto-Sell), kein Nachkauf",
                )
            )
        else:
            decisions.append(
                Decision(t, "hold", sc, f"P&L {pct:.1f}%, score {sc:.0f} – halten")
            )

    # 3) Idle-Cash: zuerst in offene Positionen mit positivem Score (Reinvest), dann max 1 Neukauf
    if cfg.get("reinvest_idle_cash", True) and pf.cash > fee * 2 + 150:
        # 3a Reinvest in beste offene Position unter TP-2%
        open_ranked = []
        for t, pos in pf.positions.items():
            if t not in prices:
                continue
            if any(d.ticker == t and d.action in ("take_profit", "dip_buy") for d in decisions):
                continue
            if pos.unrealized_pnl_pct >= tp - 2:
                continue
            sc = scores.get(t, 0.0)
            if sc >= 0:
                open_ranked.append((sc, t))
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
                    Decision(
                        t,
                        "reinvest",
                        sc,
                        f"Idle-Cash → beste offene Pos score {sc:.0f}",
                        shares=float(n),
                    )
                )

        # 3b Neukauf nur wenn noch viel Cash und Score klar positiv
        held = set(pf.positions.keys())
        candidates = [
            (sc, t)
            for t, sc in scores.items()
            if t not in held and t in prices and sc >= min_new
        ]
        candidates.sort(reverse=True)
        for sc, t in candidates[:max_new]:
            budget = pf.cash * 0.30
            n = int((budget - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(
                        t,
                        "new_buy",
                        sc,
                        f"Score {sc:.0f} >= {min_new} – Neukauf",
                        shares=float(n),
                    )
                )

    return decisions


def apply_decisions(
    pf: Portfolio, decisions: List[Decision], prices: Dict[str, float], now: datetime
) -> List[dict]:
    log = []
    for d in decisions:
        if d.action == "take_profit" and d.ticker in pf.positions and d.ticker in prices:
            ok = pf.sell(
                d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo TP: {d.reason}"
            )
            log.append(
                {
                    "ticker": d.ticker,
                    "action": "sell_tp",
                    "ok": ok,
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
