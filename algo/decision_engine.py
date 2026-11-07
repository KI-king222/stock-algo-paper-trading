"""
Active Income v4.2
- Take-Profit: bei partial_tp_fraction ≥ 0.99 → volle Position (Lohn)
- Cash-Druck: über max_cash_absolute → Neukäufe priorisieren
- Regime, Cooldown, kein Nachschuss ins starke Minus
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from portfolio import Portfolio

try:
    from algo.memory import AlgoMemory
except ImportError:
    AlgoMemory = None  # type: ignore

ACTION_DE = {
    "take_profit": "verkauf",
    "hold": "halten",
    "dip_buy": "nachkauf",
    "new_buy": "neukauf",
    "reinvest": "reinvestition",
    "skip": "überspringen",
}


@dataclass
class Decision:
    ticker: str
    action: str
    score: float
    reason: str
    shares: float = 0.0

    @property
    def action_de(self) -> str:
        return ACTION_DE.get(self.action, self.action)


@dataclass
class RunContext:
    recently_sold: Dict[str, datetime] = field(default_factory=dict)
    peaks: Dict[str, float] = field(default_factory=dict)
    cooldown_days: int = 2

    def mark_sold(self, ticker: str, when: datetime):
        self.recently_sold[ticker] = when

    def in_cooldown(self, ticker: str, now: datetime) -> bool:
        t0 = self.recently_sold.get(ticker)
        if not t0:
            return False
        return (now - t0) < timedelta(days=self.cooldown_days)

    def update_peak(self, ticker: str, price: float):
        prev = self.peaks.get(ticker)
        if prev is None or price > prev:
            self.peaks[ticker] = price

    def reset_peak(self, ticker: str, price: float):
        self.peaks[ticker] = price


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def decide(
    pf: Portfolio,
    prices: Dict[str, float],
    scores: Dict[str, float],
    score_details: Dict[str, Dict[str, float]],
    cfg: dict,
    now: datetime | None = None,
    memory=None,
    force_monthly_skim: bool = False,
    ctx: RunContext | None = None,
) -> List[Decision]:
    now = now or datetime.now()
    ctx = ctx or RunContext(cooldown_days=int(cfg.get("cooldown_days", 2)))
    tp = float(cfg.get("take_profit_pct", 6.0))
    partial = float(cfg.get("partial_tp_fraction", 1.0))
    full_exit = partial >= 0.99
    trail = float(cfg.get("trail_giveback_pct", 3.0))
    dip_tr = float(cfg.get("dip_trigger_pct", -12.0))
    max_dips = int(cfg.get("max_dip_buys_per_ticker", 1))
    min_dip_score = float(cfg.get("min_score_for_dip", 5))
    min_new = float(cfg.get("min_score_for_new_buy", 25))
    min_hold = float(cfg.get("min_score_hold_thesis", -30))
    max_pos_pct = float(cfg.get("max_position_pct_of_equity", 25.0))
    dip_frac = float(cfg.get("dip_cash_fraction", 0.35))
    reinvest_frac = float(cfg.get("reinvest_cash_fraction", 0.50))
    max_new = int(cfg.get("max_new_buys_per_month", 3))
    skim_pct = float(cfg.get("min_monthly_skim_pct", 6.0))
    skim_frac = float(cfg.get("monthly_skim_fraction", 1.0))
    no_buy_below = float(cfg.get("no_add_if_pnl_below_pct", -5.0))
    min_reinvest = float(cfg.get("min_score_for_reinvest", 25))
    regime_thr = float(cfg.get("regime_block_buys_if_median_mom5_below", -1.0))
    max_cash_abs = float(cfg.get("max_cash_absolute", 700.0))
    min_cash_reserve = float(cfg.get("min_cash_reserve", 500.0))
    fee = pf.fee_per_trade

    pf.update_prices(prices)
    equity = max(pf.total_equity(), 1.0)
    decisions: List[Decision] = []

    for t, px in prices.items():
        if t in pf.positions:
            ctx.update_peak(t, px)

    moms = []
    for t, det in score_details.items():
        m = det.get("m5")
        if m is not None:
            try:
                moms.append(float(m))
            except (TypeError, ValueError):
                pass
    if not moms:
        for det in score_details.values():
            if "momentum" in det:
                try:
                    moms.append(float(det["momentum"]))
                except (TypeError, ValueError):
                    pass
    med_mom = _median(moms)
    risk_off = med_mom is not None and med_mom < regime_thr

    # 1) Take-Profit full oder partial
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        sc = scores.get(t, 0.0)
        pct = pos.unrealized_pnl_pct
        px = prices[t]
        peak = ctx.peaks.get(t, px)
        giveback = ((peak - px) / peak * 100) if peak > 0 else 0.0

        if pct >= tp:
            if full_exit:
                n = float(pos.shares)
                reason = f"Vollverkauf (Lohn): +{pct:.1f}% ≥ +{tp}%"
            else:
                n = max(1.0, round(pos.shares * partial))
                n = min(n, pos.shares)
                reason = f"Teilverkauf: +{pct:.1f}% ≥ +{tp}% → ca. {partial*100:.0f}%"
            decisions.append(Decision(t, "take_profit", sc, reason, shares=float(n)))
        elif pct >= tp * 0.6 and giveback >= trail:
            if full_exit:
                n = float(pos.shares)
                reason = f"Trailing Vollverkauf: vom Hoch −{giveback:.1f}% (P&L {pct:+.1f}%)"
            else:
                n = max(1.0, round(pos.shares * partial))
                n = min(n, pos.shares)
                reason = f"Trailing: vom Hoch −{giveback:.1f}% (P&L noch {pct:+.1f}%)"
            decisions.append(Decision(t, "take_profit", sc, reason, shares=float(n)))

    if force_monthly_skim:
        for t, pos in list(pf.positions.items()):
            if t not in prices:
                continue
            if any(d.ticker == t and d.action == "take_profit" for d in decisions):
                continue
            pct = pos.unrealized_pnl_pct
            sc = scores.get(t, 0.0)
            if pct >= skim_pct:
                n = float(pos.shares) if skim_frac >= 0.99 else max(1.0, round(pos.shares * skim_frac))
                n = min(n, pos.shares)
                decisions.append(
                    Decision(t, "take_profit", sc, f"Monats-Mitnahme: +{pct:.1f}%", shares=float(n))
                )

    # 2) Dip / Halten
    for t, pos in list(pf.positions.items()):
        if t not in prices:
            continue
        if any(d.ticker == t and d.action == "take_profit" for d in decisions):
            continue
        sc = scores.get(t, 0.0)
        pct = pos.unrealized_pnl_pct
        details = score_details.get(t) or {}
        mom = float(details.get("momentum") or 0)
        adj = memory.dip_score_adjust(t) if memory and cfg.get("memory_enabled", True) else 0.0
        need = min_dip_score - adj

        if pct <= dip_tr and pos.dip_buy_count < max_dips and not risk_off:
            if sc < need or mom < -6:
                decisions.append(
                    Decision(t, "hold", sc, f"Dip-Zone, Score/Momentum zu schwach ({sc:.0f}/{mom:.0f})")
                )
            else:
                budget = pf.cash * dip_frac
                max_mv = equity * (max_pos_pct / 100.0)
                room = max_mv - pos.market_value
                spend = min(budget, room) if room > 0 else 0
                n = int((spend - fee) // prices[t]) if prices[t] > 0 else 0
                if n > 0:
                    decisions.append(
                        Decision(t, "dip_buy", sc, f"Nachkauf Dip {pct:.1f}% Score {sc:.0f}", shares=float(n))
                    )
                else:
                    decisions.append(Decision(t, "hold", sc, "Dip-Zone, kein Budget"))
        elif sc < min_hold:
            decisions.append(Decision(t, "hold", sc, f"Halten (Score schwach {sc:.0f})"))
        else:
            tag = " · Markt schwach (kein Zukauf)" if risk_off else ""
            decisions.append(
                Decision(t, "hold", sc, f"Halten · P&L {pct:+.1f}% · Score {sc:.0f}{tag}")
            )

    if risk_off:
        return decisions

    # 3) Cash-Druck: investierbares Cash über Reserve
    investable = max(0.0, pf.cash - min_cash_reserve)
    cash_pressure = pf.cash > max_cash_abs

    if cfg.get("reinvest_idle_cash", True) and investable > fee * 2 + 50:
        # 3a) Reinvest in offene Positionen (nicht nah am TP, nicht tief im Minus)
        open_ranked = []
        for t, pos in pf.positions.items():
            if t not in prices:
                continue
            if any(d.ticker == t and d.action in ("take_profit", "dip_buy") for d in decisions):
                continue
            if ctx.in_cooldown(t, now):
                continue
            if pos.unrealized_pnl_pct <= no_buy_below:
                continue
            if pos.unrealized_pnl_pct >= tp - 0.5:
                continue
            sc = scores.get(t, 0.0)
            mom = float((score_details.get(t) or {}).get("momentum") or 0)
            if mom < -4:
                continue
            pref = memory.prefer_ticker(t) if memory else 0.0
            if sc >= min_reinvest:
                open_ranked.append((sc + pref * 5 + mom * 0.4, t, sc))
        open_ranked.sort(reverse=True)
        if open_ranked:
            _, t, sc = open_ranked[0]
            budget = investable * reinvest_frac
            max_mv = equity * (max_pos_pct / 100.0)
            room = max_mv - pf.positions[t].market_value
            spend = min(budget, room) if room > 0 else 0
            n = int((spend - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(t, "reinvest", sc, f"Reinvestition in {t} (Score {sc:.0f})", shares=float(n))
                )

        # 3b) Neukäufe – bei Cash-Druck Pflicht, solange Kandidaten da sind
        held = set(pf.positions.keys())
        cands = []
        for t, sc in scores.items():
            if t in held or t not in prices:
                continue
            if ctx.in_cooldown(t, now):
                continue
            mom = float((score_details.get(t) or {}).get("momentum") or 0)
            if mom < -4:
                continue
            pref = memory.prefer_ticker(t) if memory else 0.0
            thr = min_new - (5 if cash_pressure else 0)  # etwas lockerer bei viel Cash
            if sc + pref * 3 >= thr:
                cands.append((sc + pref * 3 + mom * 0.3, t, sc))
        cands.sort(reverse=True)

        n_new = max_new if cash_pressure else max_new
        # bei starkem Cash-Druck mehr als ein Neukauf erlauben
        if cash_pressure and investable > max_cash_abs * 2:
            n_new = max(n_new, 2)

        for _, t, sc in cands[:n_new]:
            # so viel einsetzen, dass Cash Richtung max_cash_abs geht
            if cash_pressure:
                budget = min(investable * 0.35, investable - (max_cash_abs - min_cash_reserve) * 0.5)
                budget = max(budget, min(investable * 0.25, 2000))
            else:
                budget = investable * 0.22
            n = int((budget - fee) // prices[t]) if prices[t] > 0 else 0
            if n > 0:
                decisions.append(
                    Decision(
                        t,
                        "new_buy",
                        sc,
                        f"Neukauf {t} (Score {sc:.0f})" + (" · Cash-Druck" if cash_pressure else ""),
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
    ctx: RunContext | None = None,
) -> List[dict]:
    log = []
    for d in decisions:
        if d.action == "take_profit" and d.ticker in pf.positions and d.ticker in prices:
            ok = pf.sell(
                d.ticker, d.shares, prices[d.ticker], now, reason=f"Algo: {d.reason}"
            )
            if ok:
                if memory is not None:
                    memory.record_tp(d.ticker)
                if ctx is not None:
                    ctx.mark_sold(d.ticker, now)
                    ctx.reset_peak(d.ticker, prices[d.ticker])
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
            if ok and ctx is not None:
                ctx.update_peak(d.ticker, prices[d.ticker])
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
