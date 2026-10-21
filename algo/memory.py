"""Episoden-Gehirn: strukturierte Erinnerungen + Ticker-Bias (kein LLM)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional


class AlgoMemory:
    """
    Zwei Ebenen:
    1) bias/tp/dip-Zähler (schnell)
    2) episodes[] – feste Form für Lehren aus Entscheidungen
    """

    def __init__(self):
        self.tp_success: Dict[str, int] = defaultdict(int)
        self.dip_success: Dict[str, int] = defaultdict(int)
        self.dip_fail: Dict[str, int] = defaultdict(int)
        self.bias: Dict[str, float] = defaultdict(float)
        self.episodes: List[Dict[str, Any]] = []

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

    def add_episode(
        self,
        *,
        ticker: str,
        action: str,
        score: float,
        price: float,
        reason: str,
        report_summary: str = "",
        outcome: str = "offen",
        lesson: str = "",
    ):
        ep = {
            "id": f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{ticker}",
            "date": datetime.now().isoformat(timespec="seconds"),
            "ticker": ticker,
            "action": action,
            "score": round(float(score), 1),
            "price": round(float(price), 4) if price else None,
            "reason": reason[:300],
            "report_summary": (report_summary or "")[:400],
            "outcome": outcome,
            "lesson": lesson[:300],
        }
        self.episodes.append(ep)
        # max 200 Episoden
        if len(self.episodes) > 200:
            self.episodes = self.episodes[-200:]

    def relevant_episodes(self, ticker: str, limit: int = 5) -> List[Dict[str, Any]]:
        same = [e for e in self.episodes if e.get("ticker") == ticker]
        return list(reversed(same[-limit:]))

    def lessons_for(self, ticker: str) -> str:
        eps = self.relevant_episodes(ticker, 3)
        if not eps:
            return ""
        bits = []
        for e in eps:
            if e.get("lesson"):
                bits.append(e["lesson"])
            elif e.get("outcome") and e["outcome"] != "offen":
                bits.append(f"{e.get('action')}: {e['outcome']}")
        return " | ".join(bits[:3])

    def dip_score_adjust(self, ticker: str) -> float:
        return 5.0 * self.bias.get(ticker, 0.0)

    def prefer_ticker(self, ticker: str) -> float:
        return float(self.bias.get(ticker, 0.0))

    def to_dict(self) -> dict:
        return {
            "tp_success": dict(self.tp_success),
            "dip_success": dict(self.dip_success),
            "dip_fail": dict(self.dip_fail),
            "bias": dict(self.bias),
            "episodes": self.episodes,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "AlgoMemory":
        m = cls()
        if not d:
            return m
        for k, v in (d.get("tp_success") or {}).items():
            m.tp_success[k] = int(v)
        for k, v in (d.get("dip_success") or {}).items():
            m.dip_success[k] = int(v)
        for k, v in (d.get("dip_fail") or {}).items():
            m.dip_fail[k] = int(v)
        for k, v in (d.get("bias") or {}).items():
            m.bias[k] = float(v)
        m.episodes = list(d.get("episodes") or [])
        return m
