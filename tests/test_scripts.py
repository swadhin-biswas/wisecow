#!/usr/bin/env python3
"""Tests for the health monitor and the access log analyzer."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import log_analyzer
import system_health_monitor as monitor


class HealthMonitorTests(unittest.TestCase):
    def test_cpu_times(self):
        total, idle = monitor.read_cpu_times("cpu  1 2 3 4 5 6 7 8 0 0\n")
        self.assertEqual(total, 36)
        self.assertEqual(idle, 9)

    def test_cpu_percent(self):
        self.assertEqual(monitor.cpu_percent(100, 40, 200, 90), 50)
        self.assertEqual(monitor.cpu_percent(100, 40, 100, 40), 0)
        self.assertEqual(monitor.cpu_percent(0, 0, 10, 20), 0)
        self.assertEqual(monitor.cpu_percent(0, 0, 10, 0), 100)

    def test_memory_percent(self):
        text = "MemTotal: 1000 kB\nMemAvailable: 250 kB\n"
        self.assertEqual(monitor.memory_percent(text), 75)

    def test_memory_fallback(self):
        text = "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 50 kB\n"
        self.assertEqual(monitor.memory_percent(text), 80)

    def test_stat_state_handles_spaces(self):
        line = "42 (a process name) Z 1 2 3\n"
        self.assertEqual(monitor.stat_state(line), "Z")
        self.assertEqual(monitor.stat_state("broken"), "")

    def test_disk_percent_range(self):
        value = monitor.disk_percent(tempfile.gettempdir())
        self.assertGreaterEqual(value, 0)
        self.assertLessEqual(value, 100)

    def test_evaluate_alerts(self):
        metrics = {
            "cpu_percent": 91,
            "memory_percent": 10,
            "disk_percent": 10,
            "processes": 10,
            "zombies": 0,
            "missing_processes": ["sshd"],
        }
        limits = {"cpu": 80, "memory": 80, "disk": 85, "processes": 400, "zombies": 5}
        alerts = monitor.evaluate(metrics, limits)
        self.assertEqual(len(alerts), 2)
        self.assertTrue(any("cpu" in alert for alert in alerts))
        self.assertTrue(any("sshd" in alert for alert in alerts))

    def test_main_healthy_and_alert_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "health.log"
            healthy = [
                "--cpu-threshold", "100",
                "--mem-threshold", "100",
                "--disk-threshold", "100",
                "--process-threshold", "100000",
                "--zombie-threshold", "1000",
                "--sample-seconds", "0.05",
                "--log-file", str(log),
            ]
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(monitor.main(healthy), 0)
            self.assertTrue(log.exists())

            alert = healthy.copy()
            alert[alert.index("100000")] = "0"
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(monitor.main(alert), 1)
            self.assertIn("ALERT", log.read_text(encoding="utf-8"))

    def test_invalid_threshold(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                monitor.main(["--cpu-threshold", "101"])
        self.assertEqual(caught.exception.code, 2)

    def test_invalid_env_threshold(self):
        with mock.patch.dict(os.environ, {"CPU_THRESHOLD": "abc"}):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    monitor.main([])
        self.assertEqual(caught.exception.code, 2)


class LogAnalyzerTests(unittest.TestCase):
    def write_log(self, directory):
        path = Path(directory) / "access.log"
        path.write_text(
            '1.1.1.1 - - [20/Nov/2026:09:00:00 +0000] "GET / HTTP/1.1" 200 100 "-" "curl/8"\n'
            '1.1.1.1 - - [20/Nov/2026:09:01:00 +0000] "GET /missing HTTP/1.1" 404 50 "-" "curl/8"\n'
            '2.2.2.2 - - [20/Nov/2026:10:00:00 +0000] "GET /api HTTP/1.1" 500 20 "-" "Mozilla/5.0"\n'
            "not a log line\n",
            encoding="utf-8",
        )
        return path

    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = log_analyzer.summarize(self.write_log(tmp), 5)
        self.assertEqual(stats["requests"], 3)
        self.assertEqual(stats["unparsed"], 1)
        self.assertEqual(stats["not_found"], 1)
        self.assertEqual(stats["unique_ips"], 2)
        self.assertEqual(stats["bytes_sent"], 170)
        self.assertEqual(stats["top_pages"][0], ("/", 1))

    def test_human_bytes(self):
        self.assertEqual(log_analyzer.human_bytes(0), "0 B")
        self.assertEqual(log_analyzer.human_bytes(2048), "2.0 KiB")

    def test_gzip(self):
        import gzip

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "access.log.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(
                    '1.1.1.1 - - [20/Nov/2026:09:00:00 +0000] '
                    '"GET / HTTP/1.1" 200 10 "-" "curl/8"\n'
                )
            stats = log_analyzer.summarize(path, 5)
        self.assertEqual(stats["requests"], 1)


if __name__ == "__main__":
    unittest.main()
