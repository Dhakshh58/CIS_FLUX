#!/usr/bin/env python3
"""Flag new meter reads against the same customer's recent matching time slot."""

from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime, timedelta
from pathlib import Path


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
REQUIRED_COLUMNS = [
    "cust-id",
    "meter-num",
    "meter-status",
    "current-meter-read",
    "current-meter-read-timestamp",
    "previous-meter-read",
    "usage-index",
]
OUTPUT_COLUMNS = REQUIRED_COLUMNS + [
    "budget indicator",
    "baseline-count",
    "baseline-mean",
    "baseline-stddev",
    "spike-threshold",
    "spike-flag",
    "flag-reason",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")
        return list(reader)


def parse_time(row: dict[str, str], path: Path) -> datetime:
    try:
        return datetime.strptime(row["current-meter-read-timestamp"], TIMESTAMP_FORMAT)
    except (ValueError, TypeError) as error:
        raise ValueError(
            f"{path}: invalid timestamp {row.get('current-meter-read-timestamp')!r}"
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True, help="Historical usage CSV")
    parser.add_argument("--current", type=Path, required=True, help="New meter reads CSV")
    parser.add_argument("--output", type=Path, required=True, help="Results CSV to create")
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--sigma", type=float, default=3.0,
                        help="Flag above mean + sigma * population standard deviation")
    parser.add_argument("--min-history", type=int, default=30,
                        help="Matching prior readings required for a decision")
    args = parser.parse_args()

    if args.lookback_days < 1 or args.sigma <= 0 or args.min_history < 2:
        parser.error("lookback-days and sigma must be positive; min-history must be at least 2")

    history = read_csv(args.history)
    incoming = read_csv(args.current)
    if not incoming:
        raise ValueError(f"{args.current}: no incoming readings")

    parsed_history = []
    for row in history:
        timestamp = parse_time(row, args.history)
        try:
            usage = float(row["usage-index"])
        except (ValueError, TypeError) as error:
            raise ValueError(f"{args.history}: invalid usage-index {row['usage-index']!r}") from error
        parsed_history.append((row, timestamp, usage))

    results = []
    for row in incoming:
        timestamp = parse_time(row, args.current)
        try:
            usage = float(row["usage-index"])
        except (ValueError, TypeError) as error:
            raise ValueError(f"{args.current}: invalid usage-index {row['usage-index']!r}") from error

        slot = timestamp.hour  # File cadence is every four hours; minute must be 00.
        if timestamp.minute != 0 or timestamp.second != 0 or slot % 4 != 0:
            raise ValueError(
                f"{args.current}: timestamp {timestamp.strftime(TIMESTAMP_FORMAT)} "
                "is not aligned to a four-hour slot"
            )

        start = timestamp - timedelta(days=args.lookback_days)
        matching = [
            past_usage
            for past_row, past_time, past_usage in parsed_history
            if past_row["cust-id"] == row["cust-id"]
            and past_row["meter-num"] == row["meter-num"]
            and past_time.hour == slot
            and past_time.minute == timestamp.minute
            and start <= past_time < timestamp
        ]

        result = {column: row[column] for column in REQUIRED_COLUMNS}
        result["budget indicator"] = row.get("budget indicator", "")
        result["baseline-count"] = str(len(matching))
        if len(matching) < args.min_history:
            result.update({
                "baseline-mean": "",
                "baseline-stddev": "",
                "spike-threshold": "",
                "spike-flag": "INSUFFICIENT_HISTORY",
                "flag-reason": f"Need {args.min_history} matching historical readings; found {len(matching)}",
            })
        else:
            mean = sum(matching) / len(matching)
            stddev = math.sqrt(sum((value - mean) ** 2 for value in matching) / len(matching))
            threshold = mean + args.sigma * stddev
            is_spike = usage > threshold
            result.update({
                "baseline-mean": f"{mean:.4f}",
                "baseline-stddev": f"{stddev:.4f}",
                "spike-threshold": f"{threshold:.4f}",
                "spike-flag": "TRUE" if is_spike else "FALSE",
                "flag-reason": (
                    f"Usage {usage:g} exceeds same-slot threshold {threshold:.4f}"
                    if is_spike
                    else "Usage is within same-slot threshold"
                ),
            })
        results.append(result)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as destination:
        writer = csv.DictWriter(destination, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} comparison rows to {args.output}")


if __name__ == "__main__":
    main()
