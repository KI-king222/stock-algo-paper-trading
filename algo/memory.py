"""Einfaches episodisches Gedächtnis für den Algo (kein LLM)."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List


class AlgoMemory:
    """
    Merkt sich pro Ticker:
    - erfolgreiche Take-Profits / Dips
    - wie oft ein Dip danach weiter fiel (mistake-Signal)
    Beeinflusst nur Schwellen leicht – keine magische KI.
    """

    def __init__(self):
        self.tp_success: Dict[str, int] = defaultdict(int)
        self.dip_success: Dict[str, int] = defaultdict(int)
        self.dip_fail: Dict[str, int] = defaultdict(int)
        self.bias: Dict[str, float] = defaultdict(float)  # -1 .. +1

    def record_tp(self, ticker: str):
        self.tp_success[ticker] += 1
        self.bias[ticker] = min(1.0, self.bias[ticker] + 0.05)

    def record_dip(self, ticker: str):
        self.dip_success[ticker] += 1

    def record_dip_underwater(self, ticker: str, still_down: bool):
        if still_down:
            self.dip_fail[ticker] += 1
            self.bias[ticker] = max(-1.0, self.bias[ticker] - 0.08)
        else:
            self.bias[ticker] = min(1.0, self.bias[ticker] + 0.04)

    def dip_score_adjust(self, ticker: str) -> float:
        """Zusatz auf min_score_for_dip (positiv = lockerer)."""
        return 5.0 * self.bias.get(ticker, 0.0)

    def prefer_ticker(self, ticker: str) -> float:
        return float(self.bias.get(ticker, 0.0))

    def to_dict(self) -> dict:
        return {
            "tp_success": dict(self.tp_success),
            "dip_success": dict(self.dip_success),
            "dip_fail": dict(self.dip_fail),
            "bias": dict(self.bias),
        }
