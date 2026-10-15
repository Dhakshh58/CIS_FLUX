#!/usr/bin/env python3
"""Serve the local meter usage dashboard and its CSV-backed data API."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
HISTORY_FILE = ROOT / "meter_usage_5cust_3years (1).csv"
CURRENT_FILE = ROOT / "simulated_current_meter_usage.csv"
RESULTS_FILE = ROOT / "usage_spike_results.csv"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def timestamp(row: dict[str, str]) -> datetime:
    return datetime.strptime(row["current-meter-read-timestamp"], DATE_FORMAT)


def number(row: dict[str, str], field: str) -> float:
    try:
        value = float(row[field])
        return value if math.isfinite(value) else 0.0
    except (ValueError, TypeError, KeyError):
        return 0.0


def build_data() -> dict:
    history = read_csv(HISTORY_FILE)
    current = read_csv(CURRENT_FILE)
    results = read_csv(RESULTS_FILE)
    results_by_key = {
        (row.get("cust-id"), row.get("meter-num"), row.get("current-meter-read-timestamp")): row
        for row in results
    }

    by_customer: dict[str, list[dict]] = defaultdict(list)
    daily_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    slot_values: dict[tuple[str, int], list[float]] = defaultdict(list)
    max_rows: dict[str, dict] = {}

    for row in history:
        try:
            when = timestamp(row)
            usage = number(row, "usage-index")
        except (ValueError, TypeError, KeyError):
            continue
        customer = row.get("cust-id", "Unknown")
        day = when.strftime("%Y-%m-%d")
        by_customer[customer].append({"date": day, "time": when.strftime("%H:%M"), "usage": usage})
        daily_values[(customer, day)].append(usage)
        slot_values[(customer, when.hour)].append(usage)
        if customer not in max_rows or usage > max_rows[customer]["usage"]:
            max_rows[customer] = {"usage": usage, "date": day, "time": when.strftime("%H:%M")}

    customers = sorted(set(by_customer) | {row.get("cust-id", "") for row in current})
    daily_series = []
    customer_stats = {}
    slot_stats = {}
    for customer in customers:
        usage_list = [item["usage"] for item in by_customer.get(customer, [])]
        usage_sorted = sorted(usage_list)
        daily_list = []
        for (cust, day), readings in daily_values.items():
            if cust != customer:
                continue
            daily_list.append({
                "date": day,
                "average": sum(readings) / len(readings),
                "total": sum(readings),
                "count": len(readings),
            })
        daily_list.sort(key=lambda item: item["date"])
        daily_series.extend({"customer": customer, **item} for item in daily_list)
        average = sum(usage_list) / len(usage_list) if usage_list else 0
        daily_averages = [item["average"] for item in daily_list]
        customer_stats[customer] = {
            "readings": len(usage_list),
            "days": len(daily_list),
            "mean": average,
            "min": min(usage_list) if usage_list else 0,
            "max": max_rows.get(customer, {}).get("usage", 0),
            "maxAt": max_rows.get(customer),
            "p99": usage_sorted[min(len(usage_sorted) - 1, int(0.99 * len(usage_sorted)))] if usage_sorted else 0,
            "dailyAverage": sum(daily_averages) / len(daily_averages) if daily_averages else 0,
        }
        for hour in range(0, 24, 4):
            values = slot_values.get((customer, hour), [])
            mean = sum(values) / len(values) if values else 0
            sd = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) if values else 0
            slot_stats[f"{customer}|{hour:02d}"] = {
                "count": len(values), "mean": mean, "stddev": sd,
            }

    current_rows = []
    for row in current:
        key = (row.get("cust-id"), row.get("meter-num"), row.get("current-meter-read-timestamp"))
        result = results_by_key.get(key, {})
        current_rows.append({
            **row,
            "usageNumber": number(row, "usage-index"),
            "flag": result.get("spike-flag", "NOT_ANALYZED"),
            "threshold": result.get("spike-threshold", ""),
            "baselineMean": result.get("baseline-mean", ""),
            "reason": result.get("flag-reason", "No matching analysis result found."),
        })

    latest = max((row.get("current-meter-read-timestamp", "") for row in current_rows), default="")
    flagged = sum(row["flag"] == "TRUE" for row in current_rows)
    current_dates = []
    observed_months: dict[str, set[str]] = defaultdict(set)
    for row in current_rows:
        try:
            when = timestamp(row)
        except (ValueError, TypeError, KeyError):
            continue
        current_dates.append(when)
        observed_months[row.get("cust-id", "Unknown")].add(when.strftime("%Y-%m"))
    expected_months = []
    if current_dates:
        cursor = min(current_dates).replace(day=1, hour=0, minute=0, second=0)
        last_month = max(current_dates).replace(day=1, hour=0, minute=0, second=0)
        while cursor <= last_month:
            expected_months.append(cursor.strftime("%Y-%m"))
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    missing_read_months = [
        {"customer": customer, "month": month,
         "monthLabel": datetime.strptime(month, "%Y-%m").strftime("%B %Y")}
        for customer in customers
        for month in expected_months
        if month not in observed_months.get(customer, set())
    ]
    missing_read_gaps = []
    if current_dates:
        first_slot = min(current_dates).replace(minute=0, second=0, microsecond=0)
        first_slot = first_slot.replace(hour=(first_slot.hour // 4) * 4)
        last_slot = max(current_dates).replace(minute=0, second=0, microsecond=0)
        last_slot = last_slot.replace(hour=(last_slot.hour // 4) * 4)
        for customer in customers:
            observed_slots = {
                when for row in current_rows
                if row.get("cust-id") == customer
                for when in [timestamp(row)]
            }
            cursor = first_slot
            gap_start = None
            gap_count = 0
            while cursor <= last_slot:
                if cursor not in observed_slots:
                    if gap_start is None:
                        gap_start = cursor
                    gap_count += 1
                elif gap_start is not None:
                    missing_read_gaps.append({
                        "customer": customer,
                        "start": gap_start.strftime(DATE_FORMAT),
                        "end": (cursor - timedelta(hours=4)).strftime(DATE_FORMAT),
                        "missingReads": gap_count,
                    })
                    gap_start = None
                    gap_count = 0
                cursor += timedelta(hours=4)
            if gap_start is not None:
                missing_read_gaps.append({
                    "customer": customer,
                    "start": gap_start.strftime(DATE_FORMAT),
                    "end": last_slot.strftime(DATE_FORMAT),
                    "missingReads": gap_count,
                })
    return {
        "historyCount": len(history),
        "currentCount": len(current_rows),
        "latest": latest,
        "customers": customers,
        "customerStats": customer_stats,
        "daily": daily_series,
        "slots": slot_stats,
        "current": current_rows,
        "flaggedCount": flagged,
        "missingReadMonths": missing_read_months,
        "missingReadCount": len(missing_read_months),
        "missingReadGaps": missing_read_gaps,
        "missingReadGapCount": len(missing_read_gaps),
        "historyStart": min((row["date"] for row in daily_series), default=""),
        "historyEnd": max((row["date"] for row in daily_series), default=""),
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/data":
            payload = json.dumps(build_data(), separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/hourly":
            query = parse_qs(parsed.query)
            customer = query.get("customer", ["all"])[0]
            days_text = query.get("days", ["90"])[0]
            days = None if days_text == "all" else max(1, min(int(days_text), 3650))
            rows = []
            for row in read_csv(HISTORY_FILE):
                try:
                    when = timestamp(row)
                except (ValueError, TypeError, KeyError):
                    continue
                if customer != "all" and row.get("cust-id") != customer:
                    continue
                rows.append({
                    "customer": row.get("cust-id", "Unknown"),
                    "timestamp": when.strftime(DATE_FORMAT),
                    "usage": number(row, "usage-index"),
                })
            if rows and days is not None:
                latest = max(datetime.strptime(row["timestamp"], DATE_FORMAT) for row in rows)
                start = latest - timedelta(days=days)
                rows = [row for row in rows if datetime.strptime(row["timestamp"], DATE_FORMAT) >= start]
            payload = json.dumps(rows, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path in ("/", "/index.html"):
            self.path = "/dashboard.html"
        return super().do_GET()

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Meter usage dashboard: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop the local server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
