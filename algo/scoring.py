"""
Score v2: Fundamentals + strukturierte Finanzbericht-Trends → −100 … +100.
Regelbasiert (kein LLM-Training) – systematische Auswertung der Zahlen.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_fundamentals(fund: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """Basis-Score aus Info-Kennzahlen."""
    parts: Dict[str, float] = {}
    known = 0

    rg = fund.get("revenue_growth")
    eg = fund.get("earnings_growth")
    eqg = fund.get("earnings_quarterly_growth")
    growth = 0.0
    if rg is not None:
        growth += _clip(rg * 100, -30, 40) * 0.45
        known += 1
    if eg is not None:
        growth += _clip(eg * 100, -40, 50) * 0.35
        known += 1
    if eqg is not None:
        growth += _clip(eqg * 100, -40, 50) * 0.2
        known += 1
    parts["growth"] = _clip(growth, -25, 25)

    pm = fund.get("profit_margins")
    om = fund.get("operating_margins")
    roe = fund.get("return_on_equity")
    prof = 0.0
    if pm is not None:
        prof += _clip((pm - 0.05) * 200, -12, 12)
        known += 1
    if om is not None:
        prof += _clip((om - 0.08) * 100, -8, 8)
        known += 1
    if roe is not None:
        prof += _clip((roe - 0.08) * 100, -12, 12)
        known += 1
    parts["profitability"] = _clip(prof, -25, 25)

    dte = fund.get("debt_to_equity")
    cr = fund.get("current_ratio")
    bal = 0.0
    if dte is not None:
        d = dte / 100.0 if dte > 10 else dte
        bal += _clip(15 - d * 8, -20, 15)
        known += 1
    if cr is not None:
        bal += _clip((cr - 1.0) * 10, -10, 10)
        known += 1
    parts["balance"] = _clip(bal, -25, 25)

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

    # Free Cashflow-Hinweis aus Info
    fcf = fund.get("free_cashflow")
    if fcf is not None:
        parts["fcf_info"] = 5.0 if fcf > 0 else -6.0
        known += 1
    else:
        parts["fcf_info"] = 0.0

    total = sum(parts.values())
    if known < 3:
        total *= 0.5
    total = _clip(total, -100, 100)
    parts["total"] = round(total, 1)
    parts["data_points"] = float(known)
    return total, parts


def score_financial_report(report: Dict[str, Any]) -> Tuple[float, Dict[str, float], str]:
    """
    Zusatscore aus Quartals-Trends (wie Bericht gelesen: Umsatz, Gewinn, Marge, Schulden, FCF).
    Rückgabe: (score −30…+30, parts, kurztext)
    """
    parts: Dict[str, float] = {}
    q = 0.0

    ry = report.get("revenue_growth_yoy")
    if ry is not None:
        # +10% YoY ≈ stark
        parts["umsatz_yoy"] = _clip(ry * 80, -12, 12)
        q += 1
    else:
        parts["umsatz_yoy"] = 0.0

    ey = report.get("earnings_growth_yoy")
    if ey is not None:
        parts["gewinn_yoy"] = _clip(ey * 60, -12, 12)
        q += 1
    else:
        parts["gewinn_yoy"] = 0.0

    md = report.get("net_margin_delta_qoq")
    if md is not None:
        parts["marge_trend"] = _clip(md * 200, -8, 8)
        q += 1
    else:
        parts["marge_trend"] = 0.0

    dt = report.get("debt_trend_qoq")
    if dt is not None:
        # Schulden runter = gut
        parts["schulden_trend"] = _clip(-dt * 40, -8, 8)
        q += 1
    else:
        parts["schulden_trend"] = 0.0

    if report.get("fcf_latest") is not None:
        parts["cashflow"] = 6.0 if report.get("fcf_positive") else -8.0
        q += 1
    else:
        parts["cashflow"] = 0.0

    sub = sum(parts.values())
    if q < 2:
        sub *= 0.4
    sub = _clip(sub, -30, 30)
    parts["report_subscore"] = round(sub, 1)
    parts["report_data_points"] = float(q)

    text = " | ".join(report.get("findings") or ["keine Trends"])
    return sub, parts, text


def score_combined(
    fund: Dict[str, Any], report: Optional[Dict[str, Any]] = None
) -> Tuple[float, Dict[str, Any], str]:
    """Gesamtscore = Fundamentals + Bericht-Trends."""
    base, base_parts = score_fundamentals(fund)
    report_text = ""
    if report and report.get("data_quality", 0) > 0:
        sub, rparts, report_text = score_financial_report(report)
        total = _clip(base * 0.7 + sub, -100, 100)
        parts = {**base_parts, **rparts, "total": round(total, 1)}
    else:
        total = base
        parts = {**base_parts, "total": round(total, 1)}
        report_text = "Keine ausreichenden Quartalsdaten."
    return total, parts, report_text
