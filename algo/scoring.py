"""Fundamentals → Score −100 … +100 (regelbasiert, kein LLM)."""
from __future__ import annotations

from typing import Any, Dict, Tuple


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_fundamentals(fund: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Gewichtete Teil-Scores. Fehlende Daten werden neutral (0) behandelt,
    viele Fehlwerte → Gesamt-Score Richtung 0 (keine falsche Sicherheit).
    """
    parts: Dict[str, float] = {}
    known = 0

    # Wachstum (−25 … +25)
    rg = fund.get("revenue_growth")
    eg = fund.get("earnings_growth")
    growth = 0.0
    if rg is not None:
        growth += _clip(rg * 100, -30, 40) * 0.5  # z.B. 0.1 → 5
        known += 1
    if eg is not None:
        growth += _clip(eg * 100, -40, 50) * 0.5
        known += 1
    parts["growth"] = _clip(growth, -25, 25)

    # Rentabilität (−25 … +25)
    pm = fund.get("profit_margins")
    roe = fund.get("return_on_equity")
    prof = 0.0
    if pm is not None:
        # 10% Marge ≈ gut
        prof += _clip((pm - 0.05) * 200, -15, 15)
        known += 1
    if roe is not None:
        prof += _clip((roe - 0.08) * 100, -15, 15)
        known += 1
    parts["profitability"] = _clip(prof, -25, 25)

    # Bilanz (−25 … +25)
    dte = fund.get("debt_to_equity")
    cr = fund.get("current_ratio")
    bal = 0.0
    if dte is not None:
        # debtToEquity oft in %
        d = dte / 100.0 if dte > 10 else dte
        bal += _clip(15 - d * 8, -20, 15)
        known += 1
    if cr is not None:
        bal += _clip((cr - 1.0) * 10, -10, 10)
        known += 1
    parts["balance"] = _clip(bal, -25, 25)

    # Bewertung leicht (−15 … +15) – teuer nicht automatisch „schlecht“ bei Wachstum
    pe = fund.get("trailing_pe") or fund.get("forward_pe")
    val = 0.0
    if pe is not None and pe > 0:
        if pe < 12:
            val = 10
        elif pe < 25:
            val = 3
        elif pe < 40:
            val = -3
        else:
            val = -10
        known += 1
    parts["valuation"] = val

    total = sum(parts.values())
    # Unsicherheit: wenige Daten → Richtung 0
    if known < 3:
        total *= 0.5
    total = _clip(total, -100, 100)
    parts["total"] = round(total, 1)
    parts["data_points"] = float(known)
    return total, parts
