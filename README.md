# stock-algo-paper-trading

**Gleiche Handelslogik** wie [`stock-paper-trading`](https://github.com/KI-king222/stock-paper-trading), aber:

| | KI-Repo | **Dieses Repo (Algo)** |
|--|---------|-------------------------|
| Entscheidung | Grok / Prompt | **Python-Algorithmus** |
| Finanzberichte | KI liest & bewertet | **Zahlen-Score** (API + Regeln) |
| Regeln | TP, Dip, kein Minus-Sell | **identisch** |
| Portfolio | JSON + Streamlit | JSON + optional Streamlit |

## Harte Regeln (kalibriert)

- **Take-Profit** ab **+12 %** vom Einstand → auto, Gewinn → `external_profit`
- **Kein Auto-Sell im Minus**
- **Nachkauf im Minus** ab ca. **−12 %**, max. **2×** pro Ticker, nur wenn Fundamentals-Score ok
- Gebühr 1 € / Trade, DE-Steuer-Simulation, Start 10 000 €

## Wie der Algorithmus „Berichte“ bewertet

Kein LLM. Stattdessen strukturierte Fundamentals (yfinance / später SEC/Bundesanzeiger):

1. **Wachstum** (Umsatz/Gewinn YoY)
2. **Rentabilität** (Marge, ROE grob)
3. **Bilanz** (Verschuldung vs. Equity)
4. **Trend** (Kurs vs. 60/200-Tage, nur als Nebenfaktor)

Daraus entsteht ein **Score −100 … +100** und eine Aktion: `hold` / `dip_buy` / `take_profit` / `watch` / `new_buy`.

## Struktur

```
portfolio.py          # Portfolio, Trades, Steuer (wie KI-Repo)
data.py               # Kurse + Fundamentals-Rohdaten
algo/
  scoring.py          # Score aus Kennzahlen
  decision_engine.py  # Regeln + Score → Aktion
  run_daily.py        # Tages-/Wochenlauf
config.json           # Schwellen, Universe
state/
  portfolio_state.json
  decisions_log.json
requirements.txt
.github/workflows/run_algo.yml
```

## Start lokal

```bash
pip install -r requirements.txt
python -m algo.run_daily
```

## GitHub Actions

**Actions → Run Algo Daily → Run workflow**

## Abgrenzung

- **Kein** Ersatz für echte Bilanzanalyse durch Menschen.
- **Kein** Look-ahead im Backtest (nur Daten ≤ T).
- Parallel zum KI-Repo betreibbar – gleicher Papiergeld-Gedanke, andere Entscheidungsquelle.

Paper-Trading. Keine Anlageberatung.
