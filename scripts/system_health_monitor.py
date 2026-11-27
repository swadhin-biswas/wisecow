#!/usr/bin/env python3
"""Check CPU, memory, disk, processes and zombies, then alert on thresholds."""

import argparse
import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOST = socket.gethostname()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_cpu_times(text):
    parts = text.splitlines()[0].split()[1:]
    values = [int(part) for part in parts[:8]]
    total = sum(values)
    idle = values[3] + values[4]
    return total, idle


def cpu_percent(total_a, idle_a, total_b, idle_b):
    total_delta = total_b - total_a
    if total_delta <= 0:
        return 0
    idle_delta = idle_b - idle_a
    usage = round(100 * (total_delta - idle_delta) / total_delta)
    return max(0, min(100, usage))


def sample_cpu(seconds):
    first = read_cpu_times(Path("/proc/stat").read_text(encoding="ascii"))
    time.sleep(seconds)
    second = read_cpu_times(Path("/proc/stat").read_text(encoding="ascii"))
    return cpu_percent(*first, *second)


def read_meminfo(text):
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        fields = rest.split()
        if fields:
            values[key] = int(fields[0])
    return values


def memory_percent(text):
    info = read_meminfo(text)
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable")
    if available is None:
        available = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
    if total <= 0:
        return 0
    usage = round(100 * (total - available) / total)
    return max(0, min(100, usage))


def disk_percent(path):
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        return 0
    percent = round(100 * usage.used / usage.total)
    return max(0, min(100, percent))


def stat_state(line):
    end = line.rfind(")")
    if end == -1:
        return ""
    fields = line[end + 2:].split()
    return fields[0] if fields else ""


def process_snapshot():
    total = 0
    zombies = 0
    names = set()

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue

        total += 1

        try:
            line = (entry / "stat").read_text(encoding="ascii", errors="replace")
        except OSError:
            continue

        if stat_state(line) == "Z":
            zombies += 1

        try:
            names.add((entry / "comm").read_text(encoding="ascii", errors="replace").strip())
        except OSError:
            pass

    return total, zombies, names


def evaluate(metrics, limits):
    alerts = []

    if metrics["cpu_percent"] > limits["cpu"]:
        alerts.append(f"cpu usage {metrics['cpu_percent']}% is above {limits['cpu']}%")

    if metrics["memory_percent"] > limits["memory"]:
        alerts.append(f"memory usage {metrics['memory_percent']}% is above {limits['memory']}%")

    if metrics["disk_percent"] > limits["disk"]:
        alerts.append(f"disk usage {metrics['disk_percent']}% is above {limits['disk']}%")

    if metrics["processes"] > limits["processes"]:
        alerts.append(f"process count {metrics['processes']} is above {limits['processes']}")

    if metrics["zombies"] > limits["zombies"]:
        alerts.append(f"zombie count {metrics['zombies']} is above {limits['zombies']}")

    for name in metrics["missing_processes"]:
        alerts.append(f"required process '{name}' is not running")

    return alerts


def prepare_log(path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        print(f"cannot write to log file {path}: {exc}", file=sys.stderr)
        return False
    return True


def append_log(path, line):
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print(f"cannot append to log file {path}: {exc}", file=sys.stderr)


def summary_line(metrics):
    load = ", ".join(str(value) for value in metrics["load"])
    return (
        f"{metrics['timestamp']} host={metrics['host']} status={metrics['status']} "
        f"cpu={metrics['cpu_percent']}% mem={metrics['memory_percent']}% "
        f"disk={metrics['disk_percent']}% procs={metrics['processes']} "
        f"zombies={metrics['zombies']} load={load} alerts={len(metrics['alerts'])}"
    )


def build_metrics(args):
    total, zombies, names = process_snapshot()
    required = sorted({
        name.strip()
        for name in args.required_processes.split(",")
        if name.strip()
    })

    metrics = {
        "timestamp": utc_now(),
        "host": HOST,
        "cpu_percent": sample_cpu(args.sample_seconds),
        "memory_percent": memory_percent(Path("/proc/meminfo").read_text(encoding="ascii")),
        "disk_percent": disk_percent(args.disk_path),
        "processes": total,
        "zombies": zombies,
        "load": [round(value, 2) for value in os.getloadavg()],
        "required_processes": required,
        "missing_processes": sorted(set(required) - names),
    }

    limits = {
        "cpu": args.cpu_threshold,
        "memory": args.mem_threshold,
        "disk": args.disk_threshold,
        "processes": args.process_threshold,
        "zombies": args.zombie_threshold,
    }

    metrics["alerts"] = evaluate(metrics, limits)
    metrics["status"] = "ALERT" if metrics["alerts"] else "OK"
    return metrics


def build_parser():
    parser = argparse.ArgumentParser(
        prog="system_health_monitor.py",
        description="Check Linux health metrics and alert when a threshold is crossed."
    )
    parser.add_argument("--cpu-threshold", type=int,
                        default=os.environ.get("CPU_THRESHOLD", "80"),
                        help="cpu percent, default 80")
    parser.add_argument("--mem-threshold", type=int,
                        default=os.environ.get("MEM_THRESHOLD", "80"),
                        help="memory percent, default 80")
    parser.add_argument("--disk-threshold", type=int,
                        default=os.environ.get("DISK_THRESHOLD", "85"),
                        help="disk percent, default 85")
    parser.add_argument("--process-threshold", type=int,
                        default=os.environ.get("PROCESS_THRESHOLD", "400"),
                        help="process count, default 400")
    parser.add_argument("--zombie-threshold", type=int,
                        default=os.environ.get("ZOMBIE_THRESHOLD", "5"),
                        help="zombie count, default 5")
    parser.add_argument("--sample-seconds", type=float,
                        default=os.environ.get("SAMPLE_SECONDS", "1"),
                        help="cpu sample window, default 1")
    parser.add_argument("--disk-path", default=os.environ.get("DISK_PATH", "/"),
                        help="filesystem to check, default /")
    parser.add_argument("--log-file", default=os.environ.get("LOG_FILE", "system-health.log"),
                        help="default ./system-health.log")
    parser.add_argument("--required-processes", default=os.environ.get("REQUIRED_PROCESSES", ""),
                        help="comma separated process names that must run")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    for label, value in (
        ("cpu threshold", args.cpu_threshold),
        ("memory threshold", args.mem_threshold),
        ("disk threshold", args.disk_threshold),
    ):
        if value < 0 or value > 100:
            parser.error(f"{label} must be between 0 and 100")

    for label, value in (
        ("process threshold", args.process_threshold),
        ("zombie threshold", args.zombie_threshold),
    ):
        if value < 0:
            parser.error(f"{label} cannot be negative")

    if args.sample_seconds <= 0:
        parser.error("sample seconds must be above zero")

    if not Path(args.disk_path).is_dir():
        parser.error(f"disk path is not a directory: {args.disk_path}")

    if not Path("/proc/stat").is_file():
        print("this script needs /proc, so it only runs on Linux", file=sys.stderr)
        return 2

    log_path = Path(args.log_file)
    if not prepare_log(log_path):
        return 2

    metrics = build_metrics(args)
    line = summary_line(metrics)

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(line)

    append_log(log_path, line)

    for alert in metrics["alerts"]:
        message = f"{metrics['timestamp']} ALERT {alert}"
        print(message, file=sys.stderr)
        append_log(log_path, message)

    return 1 if metrics["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())
