# stock-algo-paper-trading

Paper-Trading **ohne KI**: Score aus Fundamentals + feste Regeln (TP, Dip, Reinvest).

## Täglich 23 Uhr (Automation)

GitHub Action **Run Algo Daily**:

- **Zeitplan:** Mo–Fr `21:00 UTC` ≈ **23:00 CEST**
- Speichert Portfolio in `state/portfolio_state.json`
- Log in `state/decisions_log.json`
- Status in `state/last_run_status.json`

Manuell: Repo → **Actions** → **Run Algo Daily** → **Run workflow**

## Webseite (Streamlit)

1. [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Repo: `KI-king222/stock-algo-paper-trading`
3. Branch: `main`
4. Main file: **`app.py`**
5. Deploy

Die Seite liest den `state/`-Ordner aus dem Repo (nach jedem Algo-Lauf aktualisiert).

## Lokal testen

```bash
pip install -r requirements.txt
python -m algo.run_daily
streamlit run app.py
```

## Config

Regeln in `config.json` (TP, Dip, Skim, Universe …).

## Hinweis

Paper-Trading, keine echte Order. Historische Backtests ≠ Zukunft.
