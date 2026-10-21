"""Kurse, Fundamentals und strukturierte Finanzberichts-Daten (yfinance)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import yfinance as yf


def _valid(p) -> bool:
    try:
        v = float(p)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


def get_prices(tickers: List[str]) -> Dict[str, float]:
    out = {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if hist.empty:
                continue
            p = float(hist["Close"].dropna().iloc[-1])
            if _valid(p):
                out[t] = p
        except Exception as e:
            print(f"Preis {t}: {e}")
    return out


def get_fundamentals(ticker: str) -> Dict[str, Any]:
    t = yf.Ticker(ticker)
    info = {}
    try:
        info = t.info or {}
    except Exception:
        info = {}

    def g(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return None

    return {
        "ticker": ticker,
        "name": info.get("shortName") or info.get("longName") or ticker,
        "sector": info.get("sector"),
        "revenue_growth": g("revenueGrowth"),
        "earnings_growth": g("earningsGrowth"),
        "profit_margins": g("profitMargins"),
        "operating_margins": g("operatingMargins"),
        "return_on_equity": g("returnOnEquity"),
        "debt_to_equity": g("debtToEquity"),
        "current_ratio": g("currentRatio"),
        "trailing_pe": g("trailingPE"),
        "forward_pe": g("forwardPE"),
        "price_to_book": g("priceToBook"),
        "free_cashflow": g("freeCashflow"),
        "total_cash": g("totalCash"),
        "total_debt": g("totalDebt"),
        "earnings_quarterly_growth": g("earningsQuarterlyGrowth"),
        "gross_margins": g("grossMargins"),
        "ebitda_margins": g("ebitdaMargins"),
    }


def _series_last_n(df, row_names: List[str], n: int = 4) -> List[Optional[float]]:
    """Nimmt die neuesten n Spalten eines Yahoo-Financials-DataFrames."""
    if df is None or getattr(df, "empty", True):
        return []
    try:
        # Spalten = Perioden, neueste oft links
        cols = list(df.columns)[:n]
        out = []
        for c in cols:
            val = None
            for name in row_names:
                if name in df.index:
                    try:
                        v = df.loc[name, c]
                        if v is not None and math.isfinite(float(v)):
                            val = float(v)
                            break
                    except Exception:
                        continue
            out.append(val)
        return out
    except Exception:
        return []


def get_financial_report(ticker: str) -> Dict[str, Any]:
    """
    Strukturierte 'Finanzbericht'-Analyse aus GuV / Bilanz / Cashflow.
    Kein PDF-Parsing – dieselben Zahlen, die in Berichten stehen, als Zeitreihe.
    """
    t = yf.Ticker(ticker)
    quarterly_income = None
    quarterly_bs = None
    quarterly_cf = None
    try:
        quarterly_income = t.quarterly_financials
    except Exception:
        pass
    try:
        quarterly_bs = t.quarterly_balance_sheet
    except Exception:
        pass
    try:
        quarterly_cf = t.quarterly_cashflow
    except Exception:
        pass

    rev = _series_last_n(
        quarterly_income,
        ["Total Revenue", "Operating Revenue", "Revenue"],
        4,
    )
    net_income = _series_last_n(
        quarterly_income,
        ["Net Income", "Net Income Common Stockholders"],
        4,
    )
    op_income = _series_last_n(
        quarterly_income,
        ["Operating Income", "EBIT"],
        4,
    )
    total_debt = _series_last_n(
        quarterly_bs,
        ["Total Debt", "Long Term Debt", "Net Debt"],
        4,
    )
    equity = _series_last_n(
        quarterly_bs,
        ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"],
        4,
    )
    fcf = _series_last_n(
        quarterly_cf,
        ["Free Cash Flow", "Operating Cash Flow"],
        4,
    )

    def growth(vals: List[Optional[float]]) -> Optional[float]:
        # vals[0]=neueste, vals[1]=vorher
        if len(vals) >= 2 and vals[0] is not None and vals[1] not in (None, 0):
            try:
                return (vals[0] - vals[1]) / abs(vals[1])
            except Exception:
                return None
        return None

    def yoy(vals: List[Optional[float]]) -> Optional[float]:
        if len(vals) >= 4 and vals[0] is not None and vals[3] not in (None, 0):
            try:
                return (vals[0] - vals[3]) / abs(vals[3])
            except Exception:
                return None
        return None

    def margin(ni: Optional[float], r: Optional[float]) -> Optional[float]:
        if ni is not None and r not in (None, 0):
            return ni / r
        return None

    rev_q = growth(rev)
    rev_yoy = yoy(rev)
    ni_q = growth(net_income)
    ni_yoy = yoy(net_income)
    m0 = margin(net_income[0] if net_income else None, rev[0] if rev else None)
    m1 = margin(net_income[1] if len(net_income) > 1 else None, rev[1] if len(rev) > 1 else None)
    margin_delta = None
    if m0 is not None and m1 is not None:
        margin_delta = m0 - m1

    debt0 = total_debt[0] if total_debt else None
    debt1 = total_debt[1] if len(total_debt) > 1 else None
    debt_trend = None
    if debt0 is not None and debt1 not in (None, 0):
        debt_trend = (debt0 - debt1) / abs(debt1)

    fcf0 = fcf[0] if fcf else None
    fcf_pos = fcf0 is not None and fcf0 > 0

    findings = []
    if rev_yoy is not None:
        findings.append(
            f"Umsatz YoY: {rev_yoy*100:+.1f}%"
            + (" (wachsend)" if rev_yoy > 0.03 else (" (schrumpfend)" if rev_yoy < -0.03 else ""))
        )
    if ni_yoy is not None:
        findings.append(f"Gewinn YoY: {ni_yoy*100:+.1f}%")
    if margin_delta is not None:
        findings.append(
            f"Nettomarge QoQ: {margin_delta*100:+.2f} Prozentpunkte"
        )
    if debt_trend is not None:
        findings.append(
            f"Verschuldung QoQ: {debt_trend*100:+.1f}%"
        )
    if fcf0 is not None:
        findings.append(
            f"Cashflow (letzte Periode): {'positiv' if fcf_pos else 'negativ'} ({fcf0:,.0f})"
        )
    if not findings:
        findings.append("Zu wenige Quartalsdaten für Trend-Analyse.")

    return {
        "ticker": ticker,
        "revenue_series": rev,
        "net_income_series": net_income,
        "operating_income_series": op_income,
        "revenue_growth_qoq": rev_q,
        "revenue_growth_yoy": rev_yoy,
        "earnings_growth_qoq": ni_q,
        "earnings_growth_yoy": ni_yoy,
        "net_margin_latest": m0,
        "net_margin_delta_qoq": margin_delta,
        "debt_trend_qoq": debt_trend,
        "fcf_latest": fcf0,
        "fcf_positive": fcf_pos,
        "findings": findings,
        "data_quality": sum(
            1
            for x in (rev_yoy, ni_yoy, m0, debt_trend, fcf0)
            if x is not None
        ),
    }
