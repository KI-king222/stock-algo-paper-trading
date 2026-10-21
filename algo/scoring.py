"""Score v4.1: Fundamentals + Bericht + Kursverlauf (5/10/20 Tage)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_fundamentals(fund: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
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
    parts: Dict[str, float] = {}
    q = 0.0

    ry = report.get("revenue_growth_yoy")
    if ry is not None:
        parts["umsatz_yoy"] = _clip(ry * 80, -12, 12)
        q += 1
    else:
        parts["umsatz_yoy"] = 0.0

    ey = report.get("earnings_growth_yoy")
    if ey is not None:
        parts["gewinn_yoy"] = _clip(ey * 50, -12, 10)
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
        if dt > 0.15:
            parts["schulden_trend"] = _clip(-dt * 70, -15, 0)
        else:
            parts["schulden_trend"] = _clip(-dt * 45, -10, 8)
        q += 1
    else:
        parts["schulden_trend"] = 0.0

    if report.get("fcf_latest") is not None:
        parts["cashflow"] = 6.0 if report.get("fcf_positive") else -8.0
        q += 1
    else:
        parts["cashflow"] = 0.0

    if ry is not None and ry > 0 and dt is not None and dt > 0.2:
        parts["qualitaet"] = -6.0
    else:
        parts["qualitaet"] = 0.0

    sub = sum(parts.values())
    if q < 2:
        sub *= 0.4
    sub = _clip(sub, -30, 30)
    parts["report_subscore"] = round(sub, 1)
    parts["report_data_points"] = float(q)
    text = " | ".join(report.get("findings") or ["keine Trends"])
    return sub, parts, text


def score_price_path(
    m5: Optional[float],
    m10: Optional[float],
    m20: Optional[float],
) -> Tuple[float, Dict[str, float], str]:
    """
    Bewertet, wie die Aktie die letzten Tage/Wochen gelaufen ist.
    Starker Abwärtspfad → Abzug (kein blindes Nachkaufen ins fallende Messer).
    Erholung nach vorherigem Minus kann leicht positiv sein.
    """
    parts: Dict[str, float] = {}
    bits = []

    if m5 is not None:
        parts["m5"] = float(m5)
        bits.append(f"5T {m5:+.1f}%")
        if m5 < -6:
            parts["trend_5d"] = -10
        elif m5 < -3:
            parts["trend_5d"] = -6
        elif m5 < -1:
            parts["trend_5d"] = -2
        elif m5 > 5:
            parts["trend_5d"] = 4
        elif m5 > 2:
            parts["trend_5d"] = 2
        else:
            parts["trend_5d"] = 0
    else:
        parts["trend_5d"] = 0

    if m10 is not None:
        parts["m10"] = float(m10)
        bits.append(f"10T {m10:+.1f}%")
        if m10 < -10:
            parts["trend_10d"] = -9
        elif m10 < -5:
            parts["trend_10d"] = -5
        elif m10 < -2:
            parts["trend_10d"] = -2
        elif m10 > 8:
            parts["trend_10d"] = 3
        elif m10 > 3:
            parts["trend_10d"] = 2
        else:
            parts["trend_10d"] = 0
    else:
        parts["trend_10d"] = 0

    if m20 is not None:
        parts["m20"] = float(m20)
        bits.append(f"20T {m20:+.1f}%")
        if m20 < -12:
            parts["trend_20d"] = -8
        elif m20 < -6:
            parts["trend_20d"] = -4
        elif m20 > 10:
            parts["trend_20d"] = 3
        elif m20 > 4:
            parts["trend_20d"] = 1
        else:
            parts["trend_20d"] = 0
    else:
        parts["trend_20d"] = 0

    # Pfad-Logik: 20T stark minus, aber 5T schon erholt → mild positiver Bonus
    if m20 is not None and m5 is not None and m20 < -8 and m5 > 1:
        parts["erholung"] = 3
        bits.append("Erholung nach Minus")
    else:
        parts["erholung"] = 0

    # Dauerhaft fallend (alle Fenster negativ)
    neg = sum(
        1
        for x in (m5, m10, m20)
        if x is not None and x < -2
    )
    if neg >= 3:
        parts["dauerfall"] = -6
        bits.append("Dauerhaft schwach")
    elif neg >= 2:
        parts["dauerfall"] = -3
    else:
        parts["dauerfall"] = 0

    sub = _clip(
        parts["trend_5d"]
        + parts["trend_10d"]
        + parts["trend_20d"]
        + parts["erholung"]
        + parts["dauerfall"],
        -25,
        12,
    )
    parts["momentum"] = sub
    text = ", ".join(bits) if bits else "kein Kursverlauf"
    return sub, parts, text


def score_combined(
    fund: Dict[str, Any],
    report: Optional[Dict[str, Any]] = None,
    momentum: Optional[Dict[str, float]] = None,
) -> Tuple[float, Dict[str, Any], str]:
    base, base_parts = score_fundamentals(fund)
    report_text = ""
    sub = 0.0
    rparts: Dict[str, float] = {}

    if report and report.get("data_quality", 0) > 0:
        sub, rparts, report_text = score_financial_report(report)
    else:
        report_text = "Keine ausreichenden Quartalsdaten."

    path_score = 0.0
    path_text = ""
    if momentum:
        path_score, pparts, path_text = score_price_path(
            momentum.get("m5"),
            momentum.get("m10"),
            momentum.get("m20"),
        )
        rparts.update(pparts)

    # 55% Fund, 25% Bericht, 20% Kursverlauf
    total = _clip(base * 0.55 + sub * 0.75 + path_score * 1.0, -100, 100)
    parts = {**base_parts, **rparts, "total": round(total, 1)}
    full_text = report_text
    if path_text:
        full_text = f"{report_text} ‖ Kurs: {path_text}"
    return total, parts, full_text
