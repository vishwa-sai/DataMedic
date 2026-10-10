"""Optional browser end-to-end test (needs `pip install playwright` and Microsoft Edge or Chrome).

    python tests/e2e_browser.py            # runs against a temporary server
    python tests/e2e_browser.py --headed   # watch it happen
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
PORT = 5058
URL = f"http://127.0.0.1:{PORT}"
SHOTS = ROOT / "docs" / "screenshots"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots", action="store_true", help="save screenshots to docs/screenshots")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="dm_e2e_")
    server = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--port", str(PORT)], cwd=ROOT,
                              env=dict(os.environ, DATAMEDIC_DATA_DIR=tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(URL + "/api/health")
                break
            except OSError:
                time.sleep(0.2)
        if a.shots:
            SHOTS.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge", headless=not a.headed)
            except Exception:
                browser = p.chromium.launch(channel="chrome", headless=not a.headed)
            ctx = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(URL)
            shot = lambda n: page.screenshot(path=str(SHOTS / n), full_page=True) if a.shots else None

            # ---- Stage 1
            expect(page.locator("#btn-analyze")).to_be_disabled()
            shot("1-upload.png")
            page.set_input_files("#file-input", str(ROOT / "samples" / "messy_customers.csv"))
            expect(page.locator("#file-name")).to_have_text("messy_customers.csv")
            page.set_input_files("#file-input", str(ROOT / "requirements.txt"))  # unsupported type
            expect(page.locator("#upload-error")).to_be_visible()
            page.set_input_files("#file-input", str(ROOT / "samples" / "messy_customers.csv"))
            page.click("#btn-analyze")

            # ---- Stage 2
            expect(page.locator("#view-station")).to_be_visible(timeout=30000)
            expect(page.locator("#metrics")).to_contain_text("Duplicate rows")
            expect(page.locator("#panel-diagnosis")).to_contain_text("exact duplicate row")
            expect(page.locator("#panel-diagnosis")).to_contain_text("invalid email")
            shot("2-station-diagnosis.png")
            page.click("#tab-preview")
            expect(page.locator("#panel-preview table.data")).to_be_visible(timeout=15000)
            expect(page.locator("#panel-preview")).to_contain_text("Preview only")
            expect(page.locator("#panel-preview .badge-removed").first).to_contain_text("Duplicate")
            page.select_option("#opt-missing", "fill_text")
            page.fill("#opt-fill-text", "Unknown")
            expect(page.locator("#panel-preview")).to_contain_text("Unknown", timeout=15000)
            shot("3-station-preview.png")
            page.click("text=Side by side")
            expect(page.locator(".side-by-side")).to_be_visible()
            page.click("#tab-columns")
            expect(page.locator("#panel-columns table.data")).to_be_visible()

            # ---- Stage 3
            page.click("#btn-done")
            expect(page.locator("#view-export")).to_be_visible()
            expect(page.locator("#export-done")).to_be_visible(timeout=60000)
            expect(page.locator("#export-pill")).to_have_text("Completed")
            expect(page.locator("#ops-table")).to_contain_text("Remove exact duplicate rows")
            shot("4-export.png")
            with page.expect_download() as d1:
                page.click("text=Download cleaned data")
            f1 = Path(d1.value.path()).read_text(encoding="utf-8-sig")
            assert f1.splitlines()[0].startswith("customer_id,full_name,email"), f1[:80]
            assert "Unknown" in f1 and f1.count("\n") >= 9
            with page.expect_download() as d2:
                page.click("text=Download report (HTML)")
            assert "DataMedic quality report" in Path(d2.value.path()).read_text(encoding="utf-8")
            with page.expect_download() as d3:
                page.click("text=Download report (JSON)")
            assert '"rows_after": 9' in Path(d3.value.path()).read_text(encoding="utf-8")

            # back to review keeps the job; reload resumes it
            page.click("#btn-back-review")
            expect(page.locator("#completed-banner")).to_be_visible()
            page.reload()
            expect(page.locator("#view-export")).to_be_visible(timeout=15000)
            expect(page.locator("#export-done")).to_be_visible()

            # mobile layout renders
            page.set_viewport_size({"width": 390, "height": 800})
            shot("5-mobile.png")
            assert not errors, errors
            browser.close()
        print("E2E OK")
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
