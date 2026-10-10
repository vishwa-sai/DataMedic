# DataMedic — Clean Your Messy Data Without Breaking It

**Team Vertex · Team ID TRI26195 · Track: SaaSsum AI — IT & AI**

> Upload a messy Excel or CSV file. See exactly what's wrong. Fix it row by row — safely. Download a clean file plus a full report of every change.

Your original file is never touched.

---

<<<<<<< HEAD
## What it does

DataMedic scans `.csv`, `.xlsx`, or `.xls` files, counts real issues (not guesses), lets you pick treatments, previews before/after, applies changes to every row, and gives you:

- A cleaned data file (CSV or XLSX)
- An HTML report
- A JSON report

No login. No database. No API key. Just upload, review, clean, download.

---

## The three stages
=======
## 1. The three stages
>>>>>>> 7426bbe960793e0e34097fa8d5a1b6fc8eb1d4bf

**1. Upload & Analyze** — Drag, drop, or browse. The server validates the file, creates a job, and scans every row in chunks. You'll see real counts of what's wrong.

**2. Medic Station** — A diagnosis dashboard with issues labeled as *confirmed*, *likely*, or *suggestion*. Per-column profiles, treatment controls, live before/after previews. Nothing applies until you press **Done**.

**3. Export & Download** — Real progress bars, measured row counts, cancel anytime. The output is verified by re-reading it before the job is marked complete.

---

<<<<<<< HEAD
## Treatments (all optional, all previewed)
=======
## 2. The 500 MB limit (524,288,000 bytes)
>>>>>>> 7426bbe960793e0e34097fa8d5a1b6fc8eb1d4bf

- Trim whitespace · collapse repeated spaces
- Normalize column names (`snake_case`, unique, never blank)
- Remove exact duplicate rows
- Treat `N/A` / `null` / `-` as missing (off by default)
- Remove empty columns (off by default)
- Fill missing values: preserve / drop rows / fill text / fill with 0 / mean / median / mode
- Convert numeric text → numbers
- Convert dates → `YYYY-MM-DD`
- Email validation (+ optional lowercase)
- Output as CSV (UTF-8) or XLSX (typed numbers/dates)

**Fixed order:** trim → collapse spaces → placeholders → numbers → dates → email case → deduplicate → missing-value strategy.

---

<<<<<<< HEAD
## Built to be conservative

- Columns that look like IDs (`id`, `zip`, `phone`, `code`…) and values with leading zeros are **never** converted. A ZIP `01234` stays `01234`.
- A column becomes numeric only if **100%** of non-blank values are numeric. Dates need **≥ 90%** parseable values.
- Ambiguous dates (`03/04/2023`) are never guessed. If nothing proves the order, the column is skipped until you choose *Day first* or *Month first*.
- Invalid dates and unparseable values are left unchanged and counted.
- Invalid emails are **reported, never deleted**.
- Mean/median/mode/zero only fill numeric columns and warn that they invent values. Columns that can't be filled are listed as skipped — never silently ignored.
- Dropping rows and removing empty columns carry explicit warnings. They only affect the cleaned copy.

---

## The 500 MB limit

Enforced at every layer: browser, Flask, Waitress, and upload handler. A file of exactly 524,288,000 bytes processes fine. One byte over returns HTTP 413 and deletes nothing.

Large files are handled with streaming chunk reads, cross-chunk duplicate tracking (64-bit hashes), and intermediate files for statistics-based strategies. The server needs roughly 4× the file size in free disk during a job.

---

## Tech stack

- **Frontend:** HTML5 + CSS3 + vanilla JavaScript (no framework, no build step)
- **Backend:** Python 3.13 + Flask (Waitress), pandas + NumPy, openpyxl, xlrd
- **No database, no login, no paid service, no API key**

---

## Project layout

```
run.py                     # start server
requirements.txt
datamedic/                  # Flask backend package
static/                    # index.html, css, js
samples/                   # messy inputs + expected outputs
tests/                     # pytest suite (+ optional browser E2E)
scripts/                   # make_samples, benchmark, limit_check
docs/                      # ARCHITECTURE, TESTING, BENCHMARKS, DEMO, SECURITY
```

---
=======
## 3. Project layout

```
├─ run.py                     start the server (Waitress)
├─ requirements.txt
├─ datamedic\                 backend package
│  ├─ __init__.py             Flask app factory, routes, error handlers, security headers
│  ├─ config.py               every limit (500 MB, chunk sizes, timeouts)
│  ├─ jobs.py                 job store, state machine, worker pool, previews, downloads, cleanup
│  ├─ ingest.py               validation, encoding/delimiter detection, Excel → CSV streaming, chunk reader
│  ├─ analyzer.py             streaming diagnosis + issue generation
│  ├─ plan.py                 treatment-plan validation / resolution
│  ├─ pipeline.py             the repair engine (shared by preview and export)
│  ├─ patterns.py             numeric / date / email / placeholder detection helpers
│  ├─ dedupe.py               cross-chunk duplicate tracker
│  ├─ writers.py              streaming CSV/XLSX writers + output verification
│  └─ report.py               JSON + HTML quality report
├─ static\                    index.html, css\styles.css, js\app.js
├─ samples\                   messy inputs and reviewed expected outputs
├─ tests\                     pytest suite (+ optional browser E2E)
├─ scripts\                   make_samples, make_large_csv, benchmark, limit_check, make_expected, make_zip
└─ docs\                      ARCHITECTURE, TESTING, BENCHMARKS, DEMO, SECURITY, screenshots
```

## 4. HTTP API (used by the UI)
>>>>>>> 7426bbe960793e0e34097fa8d5a1b6fc8eb1d4bf

## API (used by the UI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config` | Limits and options |
| POST | `/api/jobs` | Upload, validate, start analysis |
| GET | `/api/jobs/<id>` | Status, progress, result summary |
| GET | `/api/jobs/<id>/analysis` | Full diagnosis |
| POST | `/api/jobs/<id>/preview` | Before/after sample + totals |
| POST | `/api/jobs/<id>/process` | Start processing |
| POST | `/api/jobs/<id>/cancel` | Cancel |
| DELETE | `/api/jobs/<id>` | Delete job |
| GET | `/api/jobs/<id>/download/cleaned` | Download cleaned file |
| GET | `/api/jobs/<id>/download/report` | Download HTML report |
| GET | `/api/jobs/<id>/download/report-json` | Download JSON report |

<<<<<<< HEAD
States: `uploaded → analyzing → analyzed → processing → completed`, plus `failed`.
=======
## 5. Tests
>>>>>>> 7426bbe960793e0e34097fa8d5a1b6fc8eb1d4bf

---

## Tests

```bash
python -m pytest -q                      # 79 automated tests
python tests/e2e_browser.py              # real browser walk-through
python scripts/benchmark.py --sizes 1 25 100
python scripts/limit_check.py            # 500 MB boundary test
```

<<<<<<< HEAD
---
=======
## 6. Known limitations (honest list)
>>>>>>> 7426bbe960793e0e34097fa8d5a1b6fc8eb1d4bf

## Honest limitations

<<<<<<< HEAD
- First row is always the header. Only the first non-empty Excel sheet is analyzed.
- Excel formulas are read as their last saved values. Merged cells, formatting, charts, and macros are not preserved.
- CSV rows with fewer fields than the header are padded; rows with more fields are rejected.
- Dates with 2-digit years, times without dates, and non-English month names are not auto-converted.
- European numbers (`1.234,56`) and percentages are not converted.
- Duplicate detection uses 64-bit hashes (collision probability ≈ 3 in a million for 10 M rows).
- `median` keeps numeric values in memory (capped at 30 M); `mode` caps distinct values at 2 M.
- For files > 64 MB, the preview is approximate (first 2,000 rows). The real export always processes every row.
- No authentication: anyone who can reach the server can use it. Job IDs are 128-bit random. Run on localhost or a trusted network.
- Server restarts mark running jobs as failed (state is on disk; work is not resumable).

---

## Security notes

See `docs/SECURITY.md`.

---

## Live site

**https://datamedic.onrender.com**

Built for the SaaSsum AI hackathon. Team Vertex ·
=======
## 7. Security & error-handling notes → `docs\SECURITY.md`

## 8. Hackathon material → `docs\DEMO.md` (demo script, judge explanation, slide outline, Q&A)
>>>>>>> 7426bbe960793e0e34097fa8d5a1b6fc8eb1d4bf
