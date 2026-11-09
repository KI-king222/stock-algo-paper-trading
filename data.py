"""Kurse (immer EUR), Fundamentals und Finanzberichts-Daten (yfinance)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

_EUR_SUFFIXES = (".DE", ".PA", ".AS", ".MI", ".BR", ".MC", ".HE", ".LS", ".VI", ".AT")
_GBP_SUFFIXES = (".L", ".IL")
_CHF_SUFFIXES = (".SW", ".VX")

_fx_cache: Dict[str, float] = {}


def _valid(p) -> bool:
    try:
        v = float(p)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


def native_currency(ticker: str) -> str:
    t = (ticker or "").upper()
    if any(t.endswith(s) for s in _EUR_SUFFIXES):
        return "EUR"
    if any(t.endswith(s) for s in _GBP_SUFFIXES):
        return "GBP"
    if any(t.endswith(s) for s in _CHF_SUFFIXES):
        return "CHF"
    return "USD"


def _fx_to_eur(ccy: str) -> float:
    ccy = (ccy or "EUR").upper()
    if ccy == "EUR":
        return 1.0
    if ccy in _fx_cache:
        return _fx_cache[ccy]
    pair = {"USD": "EURUSD=X", "GBP": "EURGBP=X", "CHF": "EURCHF=X"}.get(ccy)
    if not pair:
        _fx_cache[ccy] = 1.0
        return 1.0
    try:
        h = yf.Ticker(pair).history(period="5d")
        if h is None or h.empty:
            rate = 1.0
        else:
            quote = float(h["Close"].dropna().iloc[-1])
            rate = (1.0 / quote) if quote > 0 else 1.0
    except Exception as e:
        print(f"FX {pair}: {e}")
        rate = 1.0
    _fx_cache[ccy] = rate
    return rate


def to_eur(price_native: float, ticker: str) -> float:
    if not _valid(price_native):
        return price_native
    ccy = native_currency(ticker)
    if ccy == "EUR":
        return float(price_native)
    return float(price_native) * _fx_to_eur(ccy)


def _raw_live_price(ticker: str) -> Optional[float]:
    """
    Frischester verfügbarer Native-Kurs:
    1) fast_info.last_price
    2) 1d/5m Intraday letzter Close
    3) daily history letzter Close
    """
    t = yf.Ticker(ticker)
    # 1) fast_info
    try:
        fi = t.fast_info
        for attr in ("last_price", "lastPrice", "regular_market_price", "regularMarketPrice"):
            v = getattr(fi, attr, None)
            if v is None and isinstance(fi, dict):
                v = fi.get(attr)
            if _valid(v):
                return float(v)
    except Exception:
        pass
    # 2) Intraday 5m
    try:
        hi = t.history(period="1d", interval="5m", auto_adjust=True)
        if hi is not None and not hi.empty:
            p = float(hi["Close"].dropna().iloc[-1])
            if _valid(p):
                return p
    except Exception:
        pass
    # 3) Daily
    try:
        h = t.history(period="5d", auto_adjust=True)
        if h is not None and not h.empty:
            p = float(h["Close"].dropna().iloc[-1])
            if _valid(p):
                return p
    except Exception as e:
        print(f"Preis {ticker}: {e}")
    return None


def get_prices(tickers: List[str]) -> Dict[str, float]:
    """Aktuellste Kurse in EUR."""
    out: Dict[str, float] = {}
    for t in tickers:
        raw = _raw_live_price(t)
        if raw is None:
            continue
        out[t] = round(to_eur(raw, t), 4)
    return out


def get_price_native_and_eur(ticker: str) -> Tuple[Optional[float], Optional[float], str]:
    native = _raw_live_price(ticker)
    ccy = native_currency(ticker)
    if native is None:
        return None, None, ccy
    return native, round(to_eur(native, ticker), 4), ccy


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
        "currency": info.get("currency") or native_currency(ticker),
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
    if df is None or getattr(df, "empty", True):
        return []
    try:
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
    t = yf.Ticker(ticker)
    quarterly_income = quarterly_bs = quarterly_cf = None
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

    rev = _series_last_n(quarterly_income, ["Total Revenue", "Operating Revenue", "Revenue"], 4)
    net_income = _series_last_n(quarterly_income, ["Net Income", "Net Income Common Stockholders"], 4)
    total_debt = _series_last_n(quarterly_bs, ["Total Debt", "Long Term Debt", "Net Debt"], 4)
    fcf = _series_last_n(quarterly_cf, ["Free Cash Flow", "Operating Cash Flow"], 4)

    def yoy(vals):
        if len(vals) >= 4 and vals[0] is not None and vals[3] not in (None, 0):
            try:
                return (vals[0] - vals[3]) / abs(vals[3])
            except Exception:
                return None
        return None

    def margin(ni, r):
        if ni is not None and r not in (None, 0):
            return ni / r
        return None

    rev_yoy = yoy(rev)
    ni_yoy = yoy(net_income)
    m0 = margin(net_income[0] if net_income else None, rev[0] if rev else None)
    m1 = margin(net_income[1] if len(net_income) > 1 else None, rev[1] if len(rev) > 1 else None)
    margin_delta = (m0 - m1) if m0 is not None and m1 is not None else None
    debt0 = total_debt[0] if total_debt else None
    debt1 = total_debt[1] if len(total_debt) > 1 else None
    debt_trend = ((debt0 - debt1) / abs(debt1)) if debt0 is not None and debt1 not in (None, 0) else None
    fcf0 = fcf[0] if fcf else None
    fcf_pos = fcf0 is not None and fcf0 > 0

    findings = []
    if rev_yoy is not None:
        findings.append(f"Umsatz YoY: {rev_yoy*100:+.1f}%")
    if ni_yoy is not None:
        findings.append(f"Gewinn YoY: {ni_yoy*100:+.1f}%")
    if margin_delta is not None:
        findings.append(f"Nettomarge QoQ: {margin_delta*100:+.2f} Prozentpunkte")
    if debt_trend is not None:
        findings.append(f"Verschuldung QoQ: {debt_trend*100:+.1f}%")
    if fcf0 is not None:
        findings.append(f"Cashflow: {'positiv' if fcf_pos else 'negativ'}")
    if not findings:
        findings.append("Zu wenige Quartalsdaten für Trend-Analyse.")

    return {
        "ticker": ticker,
        "revenue_growth_yoy": rev_yoy,
        "earnings_growth_yoy": ni_yoy,
        "net_margin_latest": m0,
        "net_margin_delta_qoq": margin_delta,
        "debt_trend_qoq": debt_trend,
        "fcf_latest": fcf0,
        "fcf_positive": fcf_pos,
        "findings": findings,
        "data_quality": sum(1 for x in (rev_yoy, ni_yoy, m0, debt_trend, fcf0) if x is not None),
    }
