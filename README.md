# DataMedic — Intelligent Data Quality & Repair

**Team Vertex · Team ID TRI26195 · Track: SaaSsum AI — IT & AI · Portal: https://tricity.centle.in**

> Upload messy Excel or CSV files and clean, normalize, deduplicate and restructure them automatically.

DataMedic scans an uploaded `.csv`, `.xlsx` or `.xls` file, shows what is wrong with it (with real counts),
lets you choose the treatments, previews before/after, applies them to **every row**, and gives you a
cleaned file plus a report of exactly what changed. The original file is never modified.

* Frontend: HTML5 + CSS3 + vanilla JavaScript (no framework, no build step)
* Backend: Python 3.13 + Flask (served by Waitress), pandas + NumPy, openpyxl, xlrd
* No database, no login, no paid service, no API key

---

## 1. Run it (Windows PowerShell)

Project location: `D:\Hackathon\DataMedic`

```powershell
cd D:\Hackathon\DataMedic

# one-time setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install -r requirements.txt

# start the app and open the browser
python run.py --open
```

Open <http://127.0.0.1:5000> if the browser does not open by itself. Stop with `Ctrl+C`.

Next time, only these are needed:

```powershell
cd D:\Hackathon\DataMedic
.\.venv\Scripts\Activate.ps1
python run.py --open
```

Options: `python run.py --port 8080`, `--host 0.0.0.0` (let other devices on your network connect — only on a trusted network, there is no login).

Tested with **Python 3.13.14**: Flask 3.1.3, pandas 3.0.6, NumPy 2.5.3, openpyxl 3.1.5, xlrd 2.0.2, Waitress 3.0.2 (all installed from wheels, no compiler needed).

### Try it in 60 seconds
Sample files are in `samples\`: `messy_customers.csv` / `.xlsx` / `.xls`, `dates_and_numbers.csv`, plus deliberately broken files
(`malformed.csv`, `empty.csv`, `encoding_cp1252.csv`). Reviewed expected outputs are in `samples\expected\`.
Regenerate samples: `python scripts\make_samples.py`.

---

## 2. The three stages

| Stage | What happens |
|---|---|
| **1 Upload & Analyze** | Drag-and-drop or browse. File name/size/type shown. Real upload progress (browser `XMLHttpRequest` events). Client **and** server enforce type and the 500 MB limit. The server validates the file, creates a job id and scans every row (chunked). |
| **2 Medic Station** | Diagnosis dashboard (all numbers counted from your data), issues labelled **confirmed / likely / suggestion** with *what / why / treatment*, per-column profile, treatment controls, live **before/after preview**, then **Done & Prepare Download**. |
| **3 Export & Download** | Real elapsed timer, indeterminate bar, named stages, a measured row-progress bar only when the backend can really measure it, cancel button. Output is **verified by re-reading it** before the job is marked complete. Separate downloads: cleaned data, HTML report, JSON report. |

### Treatments (all optional, all previewed)
Trim whitespace · collapse repeated whitespace · normalize column names (snake_case, unique, never blank) ·
remove exact duplicate rows · treat `N/A`/`null`/`-` as missing (off by default) · remove empty columns (off by default) ·
missing values: **preserve** / **drop rows** / **fill with text** / **fill numeric with 0** / **mean** / **median** / **mode**
(optionally for selected columns only) · numeric text → numbers · dates → `YYYY-MM-DD` · email validation (+ optional lowercase) ·
output as CSV (UTF-8) or XLSX (typed numbers/dates).

**Order of operations** (fixed, shown in the report): trim → collapse spaces → placeholders → numbers → dates → email case →
duplicate removal (on the values *after* those steps) → missing-value strategy.

### Conservative by design
* Identifier-like columns (names containing id/zip/phone/code/…), values with leading zeros and numbers with more than 15 digits are **never** converted — a ZIP `01234` stays `01234`.
* A column becomes numeric only if **100 %** of its non-blank values are numeric. Dates require ≥ 90 % parseable values.
* `03/04/2023` is ambiguous: DataMedic reads the whole column for evidence (`13/04/…` proves day-first). If nothing proves the order, the column is **skipped** until you choose *Day first* or *Month first*. Mixed day-first and month-first columns are never guessed.
* Invalid dates (`31/02/2023`) and unparseable values are left unchanged and counted.
* Invalid emails are **reported, never deleted**.
* Mean/median/mode/zero only fill purely numeric (or any, for mode) columns and warn that they invent values. Columns that cannot be filled are listed as skipped, never silently ignored.
* “Dropping rows” and “removing empty columns” carry explicit warnings; they only affect the cleaned **copy**.

---

## 3. The 500 MB limit (524,288,000 bytes)

Defined once in `datamedic/config.py` (`MAX_FILE_BYTES`) and enforced consistently:

| Layer | Enforcement |
|---|---|
| Browser | `static/js/app.js` pre-checks size/type (`DEFAULT_MAX`, overwritten by `/api/config`) |
| Flask | `MAX_CONTENT_LENGTH = 500 MB + 1 MiB multipart overhead`; HTTP 413 handled with a friendly JSON message |
| Waitress | `max_request_body_size = MAX_CONTENT_LENGTH` in `run.py` (default would be 1 GB) |
| Upload handler | exact byte check of the saved file (`> 524,288,000` → 413, files deleted) |

No reverse proxy is used. If you put nginx/IIS in front, set `client_max_body_size 501m;` (nginx) or `maxAllowedContentLength` (IIS).

**Measured on the real server** (`scripts\limit_check.py`, Python 3.13.14, Windows, 15.7 GB RAM):
a file of exactly **524,288,000 bytes** → HTTP 201, analysed (7,698,189 rows) in 182 s, cleaned in 107 s, server peak memory **383 MB**;
a file of **524,288,001 bytes** → HTTP 413, nothing left on disk. Raw results: `docs\LIMIT_CHECK_RESULT.json`.

### How large files are handled
* CSV is streamed with `pandas.read_csv(chunksize=…)`; chunk size adapts to column count (≈2 M cells/chunk). Nothing but a small preview is sent to the browser.
* **Cross-chunk correctness:** duplicates are tracked with a sorted array of 64-bit row hashes (8 bytes per distinct row); mean/sum/count, median values and mode counters are accumulated across chunks. A test runs the same data with 7-row chunks and with one chunk and requires identical output.
* Statistics-based strategies (mean/median/mode/zero) need the stats **after** de-duplication, so the engine writes an intermediate file and does a second pass (progress is reported per pass).
* XLSX/XLS: read through openpyxl's `read_only` streaming mode into an intermediate CSV (first non-empty sheet). Guards: zip-bomb check (≤ 2 GB uncompressed XML, compression ratio), column cap, conversion timeout, `.xls` ≤ 100 MB (xlrd loads the whole sheet), encrypted files rejected.
* Bounded concurrency: 2 worker threads for jobs, 2 concurrent previews; job-specific folders under `instance\jobs\<id>\`; stale jobs (24 h) and orphan folders are deleted; temp/intermediate files are removed in `finally` blocks; 1-hour processing timeout; cooperative cancel.

**Honest limits** — see section 7 and `docs\BENCHMARKS.md`. Not every 500 MB file will work on every machine: wide files, XLSX files that expand hugely, or `median` on tens of millions of values need more RAM.

---

## 4. Project layout

```
D:\Hackathon\DataMedic
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

## 5. HTTP API (used by the UI)

| Method & path | Purpose |
|---|---|
| `GET /api/config` | limits and options |
| `POST /api/jobs` (multipart `file`) | upload, validate, start analysis → `201` job |
| `GET /api/jobs/<id>` | status, progress, elapsed time, result summary, which downloads exist |
| `GET /api/jobs/<id>/analysis` | full diagnosis |
| `POST /api/jobs/<id>/preview` `{plan}` | before/after sample + exact (or labelled-approximate) totals |
| `POST /api/jobs/<id>/process` `{plan}` | start processing → `202` |
| `POST /api/jobs/<id>/cancel` · `POST …/analyze` (retry) · `DELETE /api/jobs/<id>` | control |
| `GET /api/jobs/<id>/download/cleaned \| report \| report-json` | only while `state == completed` and file exists, else `409` |

States: `uploaded → analyzing → analyzed → processing → completed`, `failed` (with `failed_stage`). Illegal transitions return `409`.

## 6. Tests

```powershell
python -m pytest -q                      # 79 automated tests, ~10 s
pip install playwright                   # optional, once
python tests\e2e_browser.py              # real-browser walk through all 3 stages (uses Microsoft Edge/Chrome)
python scripts\benchmark.py --sizes 1 25 100
python scripts\limit_check.py            # real 500 MB boundary test (~5 min, needs ~3 GB temp disk)
```
See `docs\TESTING.md` for the coverage matrix, the manual checklist and the list of what is **not** tested.

## 7. Known limitations (honest list)

* The **first row is always the header**. Only the first non-empty sheet of a workbook is analysed (other sheets are listed in a warning). Fully blank rows in Excel are skipped; CSV rows consisting only of delimiters are kept as data.
* Excel formulas are read as their last *saved* values (a workbook that was never opened in Excel may have none). Merged cells, formatting, charts and macros are not preserved; output is a flat table.
* CSV rows with **fewer** fields than the header are padded with empty cells (pandas behaviour); rows with **more** fields are rejected with a clear error.
* Dates with 2-digit years, times without dates and non-English month names are not auto-converted. Out-of-range years (< 1677 or > 2262) are left unchanged.
* European numbers (`1.234,56`) and percentages are not converted (they are flagged as mixed/text, not guessed).
* Duplicate detection uses 64-bit hashes: collision probability ≈ n²/2⁶⁵ (≈ 3 in a million for 10 M rows). A collision would wrongly drop one row.
* `median` keeps all numeric values of the chosen columns in memory (capped at 30 M values); `mode` caps distinct values at 2 M per column — beyond that the job fails or the column is skipped with a message.
* For files > 64 MB the preview is computed on the first 2,000 rows and is **labelled as approximate**; the real export always processes every row.
* The 500 MB limit is a *file-size* limit. The server needs free disk of roughly 4× the file size during a job (upload spool + source + intermediate + output). Large `.xlsx` output (≤ 1,048,575 rows) is slow.
* No authentication: anyone who can reach the server can use it (and see job ids they know). Job ids are 128-bit random. Run it on localhost or a trusted network.
* Server restarts mark running jobs as failed (state is on disk; work is not resumable).

## 8. Security & error-handling notes → `docs\SECURITY.md`

## 9. Hackathon material → `docs\DEMO.md` (demo script, judge explanation, slide outline, Q&A)
