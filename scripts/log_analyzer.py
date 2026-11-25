#!/usr/bin/env python3
"""Summarise nginx/Apache access logs in the combined log format."""

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+)[^"]*" '
    r'(?P<status>\d{3}) (?P<size>\S+)'
    r'(?: "(?P<referer>[^"]*)" "(?P<agent>[^"]*)")?'
)


def open_log(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def human_bytes(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def summarize(path, top_n):
    total = 0
    unparsed = 0
    bytes_sent = 0
    errors = 0

    ips = Counter()
    pages = Counter()
    not_found = Counter()
    statuses = Counter()
    hours = Counter()
    agents = Counter()

    with open_log(path) as handle:
        for line in handle:
            match = LINE_RE.match(line)
            if not match:
                unparsed += 1
                continue

            total += 1
            ip = match.group("ip")
            path_hit = match.group("path")
            status = match.group("status")
            size = match.group("size")
            agent = match.group("agent")

            ips[ip] += 1
            pages[path_hit] += 1
            statuses[status] += 1
            if status.startswith("4") or status.startswith("5"):
                errors += 1
            if status == "404":
                not_found[path_hit] += 1

            if size.isdigit():
                bytes_sent += int(size)

            if agent:
                agents[agent] += 1

            try:
                when = datetime.strptime(match.group("ts"), "%d/%b/%Y:%H:%M:%S %z")
                hours[when.strftime("%Y-%m-%d %H:00")] += 1
            except ValueError:
                hours["unknown"] += 1

    return {
        "file": str(path),
        "requests": total,
        "unparsed": unparsed,
        "unique_ips": len(ips),
        "bytes_sent": bytes_sent,
        "error_rate_pct": round(100 * errors / total, 1) if total else 0.0,
        "status_classes": dict(sorted(statuses.items())),
        "not_found": statuses.get("404", 0),
        "top_not_found": not_found.most_common(top_n),
        "top_pages": pages.most_common(top_n),
        "top_ips": ips.most_common(top_n),
        "top_agents": agents.most_common(top_n),
        "busiest_hour": hours.most_common(1),
    }


def print_rows(title, rows, empty="(none)"):
    print(f"\n{title}")
    if not rows:
        print(f"  {empty}")
        return
    for name, count in rows:
        print(f"  {count:>7}  {name}")


def print_report(stats):
    print(f"log: {stats['file']}")
    print(f"requests: {stats['requests']}   unparsed lines: {stats['unparsed']}")
    print(f"unique clients: {stats['unique_ips']}")
    print(f"data sent: {human_bytes(stats['bytes_sent'])}")
    print(f"error rate: {stats['error_rate_pct']}%")
    print(f"404 responses: {stats['not_found']}")

    busiest = stats["busiest_hour"]
    if busiest:
        print(f"busiest hour: {busiest[0][0]} ({busiest[0][1]} requests)")

    print("\nstatus classes")
    if not stats["status_classes"]:
        print("  (none)")
    for code, count in stats["status_classes"].items():
        share = 100 * count / stats["requests"] if stats["requests"] else 0
        print(f"  {code}  {count:>7}  {share:5.1f}%")

    print_rows("top pages", stats["top_pages"])
    print_rows("top client addresses", stats["top_ips"])
    print_rows("top 404 paths", stats["top_not_found"])
    print_rows("top user agents", stats["top_agents"])


def main():
    parser = argparse.ArgumentParser(
        description="Summarise an nginx or Apache access log.",
        epilog="example: log_analyzer.py /var/log/nginx/access.log --top 5",
    )
    parser.add_argument("logfile", type=Path)
    parser.add_argument("--top", type=int, default=10, metavar="N", help="rows per section, default 10")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a report")
    args = parser.parse_args()

    if args.top < 1:
        parser.error("--top must be 1 or more")
    if not args.logfile.is_file():
        parser.error(f"no such file: {args.logfile}")

    stats = summarize(args.logfile, args.top)

    if stats["requests"] == 0:
        print("no lines matched the combined log format", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print_report(stats)

    return 0


if __name__ == "__main__":
    sys.exit(main())
