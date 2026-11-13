# Lenskart Growth Intelligence — Traffic Funnel Demo

A small FastAPI + SQLite demo for diagnosing changes in app/web acquisition and checkout conversion. All data is synthetic and labelled as such.

## Run locally (Windows PowerShell)

```powershell
cd lenskart-demo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app --seed
python -m uvicorn app:app --reload --port 8000
```

Open http://localhost:8000

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app --seed
uvicorn app:app --reload --port 8000
```

## What happens on startup

- Seeds actual hourly funnel data from **now - 60 days** through the last completed hour.
- Seeds forecast-only hourly data from **now** to **now + 7 days**. Forecast rows are never included in actual KPIs or conversion rates.
- Starts a small simulated live-event stream (one new segment every few seconds).
- Starts a background diagnostic worker. It runs independently of the browser and records scheduled runs in SQLite.

The dashboard refreshes every 15 seconds. Use **Run diagnosis** to invoke the same diagnostic pipeline on demand.

## Reseed

Use the dashboard's **Reseed demo data** action, or run:

```bash
python -m app --seed --reset
```

The database is created at `data/lenskart.db`. Delete that file to start completely fresh.

## Demo endpoints

- `GET /api/overview?days=30` — KPIs, previous-period comparison, funnel, forecast totals
- `GET /api/trend?days=30` — daily actual trend
- `GET /api/diagnostics` — latest hypotheses
- `POST /api/diagnostics/run` — execute a diagnostic now
- `GET /api/runs` — agent run history
- `POST /api/seed` — reset and reseed synthetic data
- `GET /api/health` — health and row counts

## Notes

This is an interview demo, not a production analytics system. The diagnostic engine is deterministic and evidence-led so it can run locally without API keys or network access. Its output is a ranked set of hypotheses, not proof of root cause. Validate against production event definitions, gateway responses, app versions and experiment logs before taking action.
