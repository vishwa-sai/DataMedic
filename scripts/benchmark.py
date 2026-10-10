"""Measure the real engine on synthetic data: time, peak RSS, output size.

    python scripts/benchmark.py --sizes 1 25 100            # MB
    python scripts/benchmark.py --sizes 500 --scenario heavy

Results are appended to docs/BENCHMARK_RESULTS.json (measured, not estimated).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from datamedic import create_app  # noqa: E402
from make_large_csv import generate  # noqa: E402

SCENARIOS = {
    "default": {},                                                      # trim, names, dedupe, numeric, dates
    "heavy": {"collapse_spaces": True, "placeholders_as_missing": True, "emails": {"lowercase": True},
              "missing": {"strategy": "median", "columns": [3]}},      # needs a second pass + median
    "fill_text": {"missing": {"strategy": "fill_text", "text": "Unknown"}},
    "minimal": {"trim": False, "normalize_columns": False, "remove_duplicates": False,
                "numeric": {"convert": False}, "dates": {"normalize": False}},
}


class PeakMemory:
    def __init__(self):
        self.proc = psutil.Process()
        self.peak = 0
        self._stop = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(0.05)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self.t.join()


def wait(c, jid, busy):
    while True:
        j = c.get(f"/api/jobs/{jid}").json
        if j["state"] not in busy:
            return j
        time.sleep(0.2)


def bench(size_mb: float, scenario: str, keep: bool, src: Path | None):
    work = Path(tempfile.mkdtemp(prefix="dm_bench_"))
    try:
        path = src or work / "input.csv"
        if not src:
            t = time.time()
            generate(path, int(size_mb * 1024 * 1024))
            print(f"  generated {path.stat().st_size / 1e6:.1f} MB in {time.time() - t:.1f}s")
        app = create_app(work / "data")
        c = app.test_client()
        rec = {"scenario": scenario, "plan": SCENARIOS[scenario], "input_bytes": path.stat().st_size,
               "python": sys.version.split()[0], "date": time.strftime("%Y-%m-%d")}
        base = psutil.Process().memory_info().rss
        with PeakMemory() as pm:
            t0 = time.time()
            with open(path, "rb") as f:
                r = c.post("/api/jobs", data={"file": (f, "bench.csv")}, content_type="multipart/form-data")
            jid = r.json["id"]
            j = wait(c, jid, ("analyzing", "uploaded"))
            rec["upload_and_analyze_seconds"] = round(time.time() - t0, 1)
            if j["state"] != "analyzed":
                rec.update(success=False, error=j["error"])
                return rec
            a = c.get(f"/api/jobs/{jid}/analysis").json
            rec.update(rows=a["summary"]["rows"], columns=a["summary"]["columns"],
                       duplicates_found=a["summary"]["duplicate_rows"])
            t1 = time.time()
            r = c.post(f"/api/jobs/{jid}/process", json={"plan": SCENARIOS[scenario]})
            if r.status_code != 202:
                rec.update(success=False, error=r.json)
                return rec
            j = wait(c, jid, ("processing",))
            rec["process_seconds"] = round(time.time() - t1, 1)
        rec["peak_rss_mb_incl_interpreter"] = round(pm.peak / 1024 ** 2)
        rec["baseline_rss_mb"] = round(base / 1024 ** 2)
        rec["success"] = j["state"] == "completed"
        if rec["success"]:
            m = j["result"]["metrics"]
            rec.update(rows_out=m["rows_out"], duplicates_removed=m["duplicates_removed"],
                       output_bytes=j["output"]["size"], missing_filled=m["missing_filled"])
        else:
            rec["error"] = j["error"]
        return rec
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", nargs="+", type=float, default=[1, 25])
    ap.add_argument("--scenario", default="default", choices=SCENARIOS)
    ap.add_argument("--file", type=Path, help="use an existing CSV instead of generating")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    results_path = ROOT / "docs" / "BENCHMARK_RESULTS.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else []
    for s in a.sizes:
        print(f"== {s} MB, scenario={a.scenario}")
        rec = bench(s, a.scenario, a.keep, a.file)
        print(json.dumps(rec, indent=2))
        results.append(rec)
        results_path.write_text(json.dumps(results, indent=2))
