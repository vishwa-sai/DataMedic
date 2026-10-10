# Hackathon demo kit — DataMedic (Team Vertex · TRI26195)

## 1. Five-minute live demo script

**Before you go on stage** (2 min): `cd D:\Hackathon\DataMedic; .\.venv\Scripts\Activate.ps1; python run.py --open`.
Have `samples\messy_customers.csv` and `samples\dates_and_numbers.csv` in an Explorer window. Zoom browser to 110 %.

| Time | You do | You say |
|---|---|---|
| 0:00 | Show the upload screen | "Everyone has a spreadsheet that's too messy to trust. DataMedic finds what's wrong, lets you choose the fixes, and proves what it changed. Limit: 500 MB." |
| 0:30 | Drag `messy_customers.csv` in, point at name/size/type, click **Analyze** | "Real upload progress. The server then scans every row — nothing here is a mock-up." |
| 1:00 | Medic Station: read the metric tiles | "10 rows, 19 missing cells, 1 exact duplicate, 2 invalid emails, 1 blank column name — all counted from the file." |
| 1:30 | Scroll the diagnosis: confirmed vs likely vs suggestion | "We don't cry wolf. *Confirmed* is verified; *likely* needs your judgement — e.g. `1 000` with a space isn't auto-converted; ID and phone columns are deliberately left as text so leading zeros survive." |
| 2:15 | Preview tab → point at the struck-through duplicate and red→green cells; toggle *Side by side* | "Before and after on the first rows, and the totals come from a dry run of the *same engine* that exports — Rows 10 → 9." |
| 3:00 | Set strategy **Fill with text** → `Unknown`; tick *Treat N/A as missing* | "Every control is wired to real backend logic; the preview refreshes." |
| 3:30 | Click **Done & Prepare Download** | "Now it applies to the full dataset. Timer is real; the percentage only appears when we can measure rows." |
| 4:00 | Completed: open the cleaned CSV and the HTML report; show Operations table | "Duplicates removed are counted at the moment of removal, not guessed from row counts. Original untouched." |
| 4:30 | Mention scale | "We ran an exactly-500 MB, 7.7 million-row file through the real server: 383 MB peak memory, ~5 minutes. One byte more → rejected." |

**Backup plan** if the browser misbehaves: run `python tests\e2e_browser.py --headed` (it performs the same walkthrough automatically)
or show `docs\screenshots\`.

**Showpiece #2 (30 s)** — `dates_and_numbers.csv`: `03/04/2023` is ambiguous → DataMedic refuses to guess; choose *Day first* and watch the preview change.
ZIP `01234` stays `01234`; `$1,234.00` becomes `1234.00`.

## 2. Two-minute technical explanation for judges

"DataMedic is a Flask service around one repair engine. A file becomes a *job* with its own folder and an explicit state machine
(uploaded → analyzing → analyzed → processing → completed/failed). CSVs are streamed in chunks as **text** so nothing is silently
re-typed; Excel sheets are streamed into the same chunk format. Diagnosis is a single pass that counts missing cells, whitespace,
duplicates (64-bit row hashes carried across chunks), numeric-looking text, date formats and email validity.

The same `Pipeline` class serves preview and export, so the preview is not a simulation — for files up to 64 MB it is a full dry run
that returns exact totals plus the first rows as before/after; for larger files the preview is computed on a 2,000-row sample and
labelled approximate. Cross-chunk correctness (duplicates, means, medians, modes) is covered by tests that rerun the same data with
7-row chunks and require identical output. The exact 524,288,000-byte limit is enforced in the browser, Flask, Waitress and by a
post-save byte check; we proved the boundary on the live server and measured 383 MB peak memory on a 500 MB file. We chose
conservative defaults — we never convert IDs/ZIPs/phones, never guess ambiguous dates, never delete invalid emails — and every run
produces a JSON/HTML report with requested vs applied operations, because trust in cleaned data matters more than speed."

## 3. Suggested presentation structure (≈ 7 slides)

1. **Problem** — "Analysts spend most of their time fixing files" + one ugly screenshot (`docs\screenshots\1-upload.png` / messy sample).
2. **Solution & users** — students, small businesses; three-stage flow (Upload → Medic Station → Export).
3. **Live demo** (the script above).
4. **What makes it trustworthy** — confirmed/likely/suggestion, conservative defaults, preview = engine, audit report, original untouched.
5. **Engineering for scale** — chunked streaming, cross-chunk state, 500 MB proof table (from `docs\BENCHMARKS.md`).
6. **Quality** — 79 automated tests + browser E2E; security notes; honest limitations.
7. **Roadmap** — multi-sheet selection, fuzzy/near-duplicate matching, saved cleaning recipes, DuckDB/process-pool speed-ups, login/teams.

### Talking points that win rubrics
* "Every number on screen is computed from your data" (show the report).
* "We tell you what we did **not** change and why" (skipped operations, protected columns).
* "Preview and export share one engine; tests assert they match."
* "We tested the actual 500 MB boundary, not just the constant."

## 4. Likely questions
* **What if my file is bigger than RAM?** CSV is streamed; memory is ~O(distinct rows × 8 bytes) plus one chunk. Measured 383 MB for 500 MB.
* **Why not an LLM/AI to clean?** Deterministic rules are auditable and reproducible; AI could later *suggest* treatments, never apply them silently.
* **How do you avoid corrupting data?** Text-only reads, 100 %-numeric rule, ID/leading-zero protection, evidence-based date order, skip-and-warn.
* **Is it secure?** See `docs\SECURITY.md`; no auth yet — runs locally.
* **What doesn't work?** README §7 lists limits (first row = header, first sheet only, 2-digit years, European decimals…).
