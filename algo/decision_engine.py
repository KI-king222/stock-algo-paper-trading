"""Active Income: Teil-TP, Monats-Skim, Memory – Aktionen auf Deutsch."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from portfolio import Portfolio

try:
    from algo.memory import AlgoMemory
except ImportError:
    AlgoMemory = None  # type: ignore

# Interne Codes → Anzeige DE
ACTION_DE = {
    "take_profit": "teilverkauf",
    "hold": "halten",
    "dip_buy": "nachkauf",
    "new_buy": "neukauf",
    "reinvest": "reinvestition",
    "skip": "überspringen",
}


@dataclass
class Decision:
    ticker: str
    action: str  # intern: take_profit | hold | dip_buy | new_buy | reinvest
    score: float
    reason: str
    shares: float = 0.0

    @property
    def action_de(self) -> str:
        return ACTION_DE.get(self.action, self.action)


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
                    f"Teilverkauf: +{pct:.1f}% ≥ +{tp}% → ca. {partial*100:.0f}% der Position",
                    shares=float(n),
                )
            )

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
                        f"Monats-Mitnahme: +{pct:.1f}% ≥ +{skim_pct}% → ca. {skim_frac*100:.0f}%",
                        shares=float(n),
                    )
                )

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
                        f"Nachkauf im Minus ({pct:.1f}%), Score {sc:.0f}",
                        shares=float(n),
                    )
                )
            else:
                decisions.append(
                    Decision(t, "hold", sc, "Dip-Zone, aber kein freies Budget")
                )
        elif sc < min_hold:
            decisions.append(
                Decision(
                    t,
                    "hold",
                    sc,
                    f"Halten (Score schwach {sc:.0f}) – kein Verkauf im Minus",
                )
            )
        else:
            decisions.append(
                Decision(
                    t,
                    "hold",
                    sc,
                    f"Halten · P&L {pct:+.1f}% · Score {sc:.0f}",
                )
            )

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
                    Decision(
                        t,
                        "reinvest",
                        sc,
                        f"Reinvestition restliches Cash in {t} (Score {sc:.0f})",
                        shares=float(n),
                    )
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
                    Decision(
                        t,
                        "new_buy",
                        sc,
                        f"Neukauf {t} (Score {sc:.0f})",
                        shares=float(n),
                    )
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
                d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo: {d.reason}"
            )
            if ok and memory is not None:
                memory.record_tp(d.ticker)
            log.append(
                {
                    "ticker": d.ticker,
                    "aktion": d.action_de,
                    "ok": ok,
                    "stück": d.shares,
                    "grund": d.reason,
                    "score": round(d.score, 1),
                }
            )
        elif d.action in ("dip_buy", "reinvest", "new_buy") and d.shares > 0 and d.ticker in prices:
            is_dip = d.action == "dip_buy"
            ok = pf.buy(
                d.ticker,
                d.shares,
                prices[d.ticker],
                now,
                reason=f"Algo: {d.reason}",
                is_dip=is_dip,
            )
            if ok and is_dip and memory is not None:
                memory.record_dip(d.ticker)
            log.append(
                {
                    "ticker": d.ticker,
                    "aktion": d.action_de,
                    "ok": ok,
                    "stück": d.shares,
                    "grund": d.reason,
                    "score": round(d.score, 1),
                }
            )
        else:
            log.append(
                {
                    "ticker": d.ticker,
                    "aktion": d.action_de,
                    "ok": True,
                    "grund": d.reason,
                    "score": round(d.score, 1),
                }
            )
    return log
