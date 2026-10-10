"""Regenerate samples/expected/*.csv from the current engine.

ONLY run this after reviewing the output by eye; tests/test_samples_expected.py then
guards against regressions.   python scripts/make_expected.py
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from datamedic import create_app  # noqa: E402
from cases import CASES  # noqa: E402


def main():
    out = ROOT / "samples" / "expected"
    out.mkdir(exist_ok=True)
    app = create_app(tempfile.mkdtemp())
    c = app.test_client()
    store = app.extensions["datamedic_store"]
    for name, (sample, plan) in CASES.items():
        with open(ROOT / "samples" / sample, "rb") as f:
            jid = c.post("/api/jobs", data={"file": (f, sample)}, content_type="multipart/form-data").json["id"]
        while c.get(f"/api/jobs/{jid}").json["state"] == "analyzing":
            time.sleep(0.05)
        c.post(f"/api/jobs/{jid}/process", json={"plan": plan})
        while c.get(f"/api/jobs/{jid}").json["state"] == "processing":
            time.sleep(0.05)
        text = (store.job_dir(jid) / "output" / "cleaned.csv").read_text(encoding="utf-8-sig")
        (out / f"{name}.csv").write_text(text, encoding="utf-8", newline="")
        print("wrote", name)


if __name__ == "__main__":
    main()
