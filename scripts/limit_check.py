"""End-to-end proof of the 500 MB limit against the REAL server (waitress).

Uploads a file of exactly 524,288,000 bytes (must be accepted, analysed and cleaned)
and one of 524,288,001 bytes (must be rejected with HTTP 413), then reports timings
and the server process's peak memory.

    python scripts/limit_check.py            # full check, ~5-10 minutes
    python scripts/limit_check.py --no-process
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from make_large_csv import generate  # noqa: E402

LIMIT = 524_288_000
PORT = 5057


def post_file(path: Path):
    boundary = "----datamedic" + os.urandom(6).hex()
    pre = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.csv"\r\n'
           f"Content-Type: text/csv\r\n\r\n").encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    size = path.stat().st_size
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=1800)
    conn.putrequest("POST", "/api/jobs")
    conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
    conn.putheader("Content-Length", str(len(pre) + size + len(post)))
    conn.endheaders()
    t0 = time.time()
    try:
        conn.send(pre)
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                conn.send(chunk)
        conn.send(post)
    except (BrokenPipeError, ConnectionError, OSError) as exc:
        print("   (connection closed while sending:", type(exc).__name__, "- reading the server's answer)")
    resp = conn.getresponse()
    body = resp.read()
    return resp.status, json.loads(body or b"{}"), time.time() - t0


def api(method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=600)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = conn.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def wait(jid, busy):
    while True:
        _, j = api("GET", f"/api/jobs/{jid}")
        if j["state"] not in busy:
            return j
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-process", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="dm_limit_"))
    env = dict(os.environ, DATAMEDIC_DATA_DIR=str(tmp / "data"))
    server = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--port", str(PORT)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    peak = {"rss": 0}
    stop = threading.Event()

    def watch():
        p = psutil.Process(server.pid)
        while not stop.is_set():
            try:
                rss = p.memory_info().rss + sum(c.memory_info().rss for c in p.children(recursive=True))
                peak["rss"] = max(peak["rss"], rss)
            except psutil.Error:
                pass
            time.sleep(0.1)

    threading.Thread(target=watch, daemon=True).start()
    report = {}
    try:
        for _ in range(100):
            try:
                if api("GET", "/api/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        print("config:", api("GET", "/api/config")[1]["max_file_bytes"])

        ok_file, over_file = tmp / "exact.csv", tmp / "over.csv"
        t = time.time()
        generate(ok_file, LIMIT, exact=True)
        print(f"generated exactly {ok_file.stat().st_size:,} bytes in {time.time() - t:.0f}s")

        status, body, secs = post_file(ok_file)
        print(f"upload of {LIMIT:,} bytes -> HTTP {status} in {secs:.0f}s")
        report["at_limit"] = {"bytes": LIMIT, "http_status": status, "upload_seconds": round(secs, 1)}
        assert status == 201, body
        jid = body["id"]
        t = time.time()
        j = wait(jid, ("analyzing", "uploaded"))
        report["at_limit"]["analyze_seconds"] = round(time.time() - t, 1)
        print("analysis:", j["state"], f"{time.time() - t:.0f}s", j.get("summary", {}).get("rows"), "rows")
        assert j["state"] == "analyzed", j["error"]
        if not a.no_process:
            t = time.time()
            status, _ = api("POST", f"/api/jobs/{jid}/process", {"plan": {}})
            assert status == 202
            j = wait(jid, ("processing",))
            report["at_limit"]["process_seconds"] = round(time.time() - t, 1)
            print("processing:", j["state"], f"{time.time() - t:.0f}s")
            assert j["state"] == "completed", j["error"]
            m = j["result"]["metrics"]
            report["at_limit"].update(rows_in=m["rows_in"], rows_out=m["rows_out"],
                                      duplicates_removed=m["duplicates_removed"], output_bytes=j["output"]["size"])
        api("DELETE", f"/api/jobs/{jid}")
        ok_file.unlink()

        generate(over_file, LIMIT + 1, exact=True)
        status, body, secs = post_file(over_file)
        print(f"upload of {LIMIT + 1:,} bytes -> HTTP {status}: {body.get('error', {}).get('message')}")
        report["over_limit"] = {"bytes": LIMIT + 1, "http_status": status, "message": body.get("error", {}).get("message")}
        assert status == 413
        left = list((tmp / "data" / "jobs").iterdir())
        report["over_limit"]["job_folders_left_behind"] = len(left)
        assert not left, "rejected upload left files behind"
    finally:
        stop.set()
        report["server_peak_rss_mb"] = round(peak["rss"] / 1024 ** 2)
        server.terminate()
        print(json.dumps(report, indent=2))
        out = ROOT / "docs" / "LIMIT_CHECK_RESULT.json"
        out.write_text(json.dumps(dict(report, date=time.strftime("%Y-%m-%d"), python=sys.version.split()[0]), indent=2))


if __name__ == "__main__":
    main()
