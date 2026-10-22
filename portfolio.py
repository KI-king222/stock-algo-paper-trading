"""Paper-Portfolio: Gebühr, Steuer, external_profit – mit Kaufdatum."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import json
import math
from pathlib import Path

DEFAULT_TAX_RATE = 0.26375
DEFAULT_FREISTELLUNG = 1000.0


def _valid_price(p) -> bool:
    try:
        v = float(p)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


@dataclass
class Trade:
    date: datetime
    ticker: str
    action: str
    shares: float
    price: float
    fee: float
    reason: str = ""
    locked_profit: float = 0.0
    tax_paid: float = 0.0

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "ticker": self.ticker,
            "action": self.action,
            "shares": self.shares,
            "price": self.price,
            "fee": self.fee,
            "reason": self.reason,
            "locked_profit": self.locked_profit,
            "tax_paid": self.tax_paid,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            date=datetime.fromisoformat(d["date"]),
            ticker=d["ticker"],
            action=d["action"],
            shares=float(d["shares"]),
            price=float(d["price"]),
            fee=float(d["fee"]),
            reason=d.get("reason", ""),
            locked_profit=float(d.get("locked_profit", 0.0)),
            tax_paid=float(d.get("tax_paid", 0.0)),
        )


@dataclass
class Position:
    ticker: str
    shares: float
    avg_cost: float
    current_price: float = 0.0
    dip_buy_count: int = 0
    opened_at: str = ""
    last_action_at: str = ""

    @property
    def market_value(self) -> float:
        px = self.current_price if _valid_price(self.current_price) else self.avg_cost
        return self.shares * float(px)

    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_cost

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_basis <= 0:
            return 0.0
        return (self.market_value - self.cost_basis) / self.cost_basis * 100.0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "shares": self.shares,
            "avg_cost": self.avg_cost,
            "current_price": self.current_price if _valid_price(self.current_price) else self.avg_cost,
            "dip_buy_count": self.dip_buy_count,
            "opened_at": self.opened_at,
            "last_action_at": self.last_action_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        cp = d.get("current_price", 0.0)
        if not _valid_price(cp):
            cp = d.get("avg_cost", 0.0)
        return cls(
            ticker=d["ticker"],
            shares=float(d["shares"]),
            avg_cost=float(d["avg_cost"]),
            current_price=float(cp),
            dip_buy_count=int(d.get("dip_buy_count", 0)),
            opened_at=str(d.get("opened_at") or ""),
            last_action_at=str(d.get("last_action_at") or ""),
        )


class Portfolio:
    def __init__(
        self,
        starting_cash: float = 10_000.0,
        fee_per_trade: float = 1.0,
        tax_rate: float = DEFAULT_TAX_RATE,
        freistellung_yearly: float = DEFAULT_FREISTELLUNG,
    ):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.fee_per_trade = fee_per_trade
        self.tax_rate = tax_rate
        self.freistellung_yearly = freistellung_yearly
        self.freistellung_remaining = freistellung_yearly
        self.external_profit = 0.0
        self.taxes_paid_total = 0.0
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []

    def _tax_on_profit(self, gross_profit: float):
        if gross_profit <= 0:
            return 0.0, 0.0, 0.0
        free = min(gross_profit, max(0.0, self.freistellung_remaining))
        taxable = gross_profit - free
        tax = round(taxable * self.tax_rate, 2)
        net = round(gross_profit - tax, 2)
        return tax, net, free

    def buy(self, ticker: str, shares: float, price: float, date: datetime, reason: str = "", is_dip: bool = False) -> bool:
        if not _valid_price(price) or shares <= 0:
            return False
        cost = shares * price
        fee = self.fee_per_trade
        total = cost + fee
        if total > self.cash + 0.01:
            return False
        self.cash -= total
        iso = date.isoformat()
        if ticker in self.positions:
            pos = self.positions[ticker]
            new_shares = pos.shares + shares
            pos.avg_cost = ((pos.shares * pos.avg_cost) + cost) / new_shares
            pos.shares = new_shares
            pos.current_price = price
            pos.last_action_at = iso
            if is_dip:
                pos.dip_buy_count += 1
        else:
            self.positions[ticker] = Position(
                ticker=ticker,
                shares=shares,
                avg_cost=price,
                current_price=price,
                dip_buy_count=1 if is_dip else 0,
                opened_at=iso,
                last_action_at=iso,
            )
        self.trades.append(
            Trade(date=date, ticker=ticker, action="buy", shares=shares, price=price, fee=fee, reason=reason)
        )
        return True

    def sell(self, ticker: str, shares: float, price: float, date: datetime, reason: str = "") -> bool:
        if ticker not in self.positions or not _valid_price(price):
            return False
        pos = self.positions[ticker]
        shares = min(shares, pos.shares)
        proceeds = shares * price
        fee = self.fee_per_trade
        net = proceeds - fee
        cost = shares * pos.avg_cost
        profit = net - cost
        locked = 0.0
        tax = 0.0
        if profit > 0:
            tax, net_after_tax, free_used = self._tax_on_profit(profit)
            self.freistellung_remaining = max(0.0, self.freistellung_remaining - free_used)
            self.cash += cost
            self.external_profit += net_after_tax
            self.taxes_paid_total += tax
            locked = net_after_tax
        else:
            self.cash += net
        iso = date.isoformat()
        pos.shares -= shares
        pos.last_action_at = iso
        if pos.shares <= 0.0001:
            del self.positions[ticker]
        self.trades.append(
            Trade(
                date=date, ticker=ticker, action="sell", shares=shares, price=price,
                fee=fee, reason=reason, locked_profit=locked, tax_paid=tax,
            )
        )
        return True

    def update_prices(self, prices: Dict[str, float]):
        for t, p in prices.items():
            if t in self.positions and _valid_price(p):
                self.positions[t].current_price = float(p)

    def total_equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def total_wealth(self) -> float:
        return self.total_equity() + self.external_profit

    def _backfill_dates(self):
        for t, pos in self.positions.items():
            if not pos.opened_at:
                for tr in self.trades:
                    if tr.ticker == t and tr.action == "buy":
                        pos.opened_at = tr.date.isoformat()
                        break
            if not pos.last_action_at:
                for tr in reversed(self.trades):
                    if tr.ticker == t:
                        pos.last_action_at = tr.date.isoformat()
                        break

    def to_dict(self) -> dict:
        self._backfill_dates()
        return {
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "fee_per_trade": self.fee_per_trade,
            "tax_rate": self.tax_rate,
            "freistellung_yearly": self.freistellung_yearly,
            "freistellung_remaining": self.freistellung_remaining,
            "external_profit": self.external_profit,
            "taxes_paid_total": self.taxes_paid_total,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "trades": [t.to_dict() for t in self.trades],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Portfolio":
        pf = cls(
            starting_cash=float(d["starting_cash"]),
            fee_per_trade=float(d.get("fee_per_trade", 1.0)),
            tax_rate=float(d.get("tax_rate", DEFAULT_TAX_RATE)),
            freistellung_yearly=float(d.get("freistellung_yearly", DEFAULT_FREISTELLUNG)),
        )
        pf.cash = float(d["cash"])
        pf.external_profit = float(d.get("external_profit", 0.0))
        pf.taxes_paid_total = float(d.get("taxes_paid_total", 0.0))
        pf.freistellung_remaining = float(d.get("freistellung_remaining", pf.freistellung_yearly))
        pf.positions = {k: Position.from_dict(v) for k, v in d.get("positions", {}).items()}
        pf.trades = [Trade.from_dict(t) for t in d.get("trades", [])]
        pf._backfill_dates()
        return pf

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str | Path) -> Optional["Portfolio"]:
        p = Path(path)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
