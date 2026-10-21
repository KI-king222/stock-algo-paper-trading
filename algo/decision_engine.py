"""Score + Portfolio-Regeln → konkrete Aktionen (kein LLM)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from portfolio import Portfolio


@dataclass
class Decision:
    ticker: str
    action: str  # take_profit | hold | dip_buy | new_buy | skip
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
    min_dip_score = float(cfg.get("min_score_for_dip", -10))
    min_new = float(cfg.get("min_score_for_new_buy", 25))
    min_hold = float(cfg.get("min_score_hold_thesis", -30))
    max_pos_pct = float(cfg.get("max_position_pct_of_equity", 40.0))
    dip_frac = float(cfg.get("dip_cash_fraction", 0.30))
    fee = pf.fee_per_trade

    pf.update_prices(prices)
    equity = pf.total_equity()
    decisions: List[Decision] = []

    # 1) Take-Profit
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

    # 2) Dip-Buy / Hold für offene Positionen
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        # schon für TP vorgesehen?
        if any(d.ticker == t and d.action == "take_profit" for d in decisions):
            continue
        sc = scores.get(t, 0.0)
        pct = pos.unrealized_pnl_pct

        if pct <= dip_tr and pos.dip_buy_count < max_dips and sc >= min_dip_score:
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
                        reason=f"Dip {pct:.1f}% <= {dip_tr}%, score {sc:.0f}, dips {pos.dip_buy_count}/{max_dips}",
                        shares=float(n),
                    )
                )
            else:
                decisions.append(
                    Decision(t, "hold", sc, f"Dip-Zone aber kein Budget/Cap | score {sc:.0f}")
                )
        elif sc < min_hold:
            decisions.append(
                Decision(
                    t,
                    "hold",
                    sc,
                    f"Score {sc:.0f} schwach – halten (kein Auto-Sell bei Minus), kein Nachkauf",
                )
            )
        else:
            decisions.append(
                Decision(t, "hold", sc, f"P&L {pct:.1f}%, score {sc:.0f} – These ok, halten")
            )

    # 3) Neukauf aus Universe wenn Cash und Score hoch
    if cfg.get("reinvest_idle_cash", True) and pf.cash > fee * 2 + 100:
        held = set(pf.positions.keys())
        candidates = []
        for t, sc in scores.items():
            if t in held or t not in prices:
                continue
            if sc >= min_new:
                candidates.append((sc, t))
        candidates.sort(reverse=True)
        for sc, t in candidates[:2]:
            # max 1/3 Cash pro Neukauf
            budget = pf.cash * 0.33
            n = int((budget - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(
                        t,
                        "new_buy",
                        sc,
                        f"Score {sc:.0f} >= {min_new} – algorithmischer Neukauf",
                        shares=float(n),
                    )
                )

    return decisions


def apply_decisions(pf: Portfolio, decisions: List[Decision], prices: Dict[str, float], now: datetime) -> List[dict]:
    log = []
    for d in decisions:
        if d.action == "take_profit" and d.ticker in pf.positions and d.ticker in prices:
            ok = pf.sell(d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo TP: {d.reason}")
            log.append({"ticker": d.ticker, "action": "sell_tp", "ok": ok, "reason": d.reason, "score": d.score})
        elif d.action == "dip_buy" and d.shares > 0 and d.ticker in prices:
            ok = pf.buy(d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo Dip: {d.reason}", is_dip=True)
            log.append({"ticker": d.ticker, "action": "dip_buy", "ok": ok, "shares": d.shares, "reason": d.reason, "score": d.score})
        elif d.action == "new_buy" and d.shares > 0 and d.ticker in prices:
            ok = pf.buy(d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo New: {d.reason}", is_dip=False)
            log.append({"ticker": d.ticker, "action": "new_buy", "ok": ok, "shares": d.shares, "reason": d.reason, "score": d.score})
        else:
            log.append({"ticker": d.ticker, "action": d.action, "ok": True, "reason": d.reason, "score": d.score})
    return log
