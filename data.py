"""Kurse und Fundamentals (yfinance) – roh, ohne Entscheidung."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Any

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
    """
    Strukturierte Kennzahlen als Proxy für 'Finanzbericht gelesen'.
    Fehlende Felder → None (Score geht konservativ damit um).
    """
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
    }
