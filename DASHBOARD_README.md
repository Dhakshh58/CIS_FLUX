# Meter usage dashboard

## Start it

Open PowerShell in this folder and run:

```powershell
python .\dashboard.py
```

Then open <http://127.0.0.1:8000>. Press `Ctrl+C` in PowerShell to stop the local server.

The server binds to `127.0.0.1` by default, so the dashboard is available only on this computer. It reads these files from this folder:

- `meter_usage_5cust_3years (1).csv` — historical meter readings
- `simulated_current_meter_usage.csv` — current-read input (currently simulated)
- `usage_spike_results.csv` — output from the spike analysis script

When current readings change, run the analyzer and refresh the dashboard:

```powershell
python .\usage_spike_prototype.py --history '.\meter_usage_5cust_3years (1).csv' --current '.\simulated_current_meter_usage.csv' --output '.\usage_spike_results.csv'
```

## Dashboard contents

- Daily mean `usage-index` trend for each customer, with 90-day, 12-month, and all-history ranges. Click the graph to open daily totals; click a date point to expand that date into its recorded four-hour readings.
- Current usage-index values alongside each read's historical threshold.
- A table showing the current reading, baseline, threshold, and spike result.
- A monthly spike-percentage chart. Its percentage is `(usage-index - spike threshold) / spike threshold * 100`; click a month to list spikes in that month, or a y-axis percentage band to list spikes in that band.
- A missing-read widget that lists customer-months with no incoming readings between the first and last months in the current feed. The demo feed intentionally omits CUST0005 for February and March 2026.
- Clickable customer IDs throughout the dashboard. Selecting one opens that customer's full history; select a date point to expand it into individual four-hour readings.
- A local data assistant for questions about customer history, current reads, high readings, flags, and recent 30-day trends.

The assistant uses data-grounded question patterns and calculations in the local dashboard. It does not call a hosted language model, so it does not provide open-ended generative answers. The current-read flag is taken from `usage_spike_results.csv`; rerun the analyzer after updating current reads.
