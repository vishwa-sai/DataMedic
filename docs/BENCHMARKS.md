# Benchmarks (measured)

Machine: Windows, 15.7 GB RAM, Python 3.13.14, pandas 3.0.6. Data: synthetic messy customer CSV from
`scripts/make_large_csv.py` (7 columns: ID, name, email, amount with currency, mixed-format dates, city, notes;
≈ 2 % exact duplicates, ≈ 20 % missing notes). Reproduce with `python scripts\benchmark.py --sizes 1 25 100`.
Raw numbers: `docs/BENCHMARK_RESULTS.json`, `docs/LIMIT_CHECK_RESULT.json`.

"Analyze" = upload + validation + full-file diagnosis. "Clean" = full treatment pass(es) + output + verification.
Peak memory = peak RSS of the whole Python process (including ≈ 90–140 MB interpreter/libraries baseline).

| Input | Rows × cols | Treatments | Analyze | Clean | Peak RSS | Output | Result |
|---|---|---|---|---|---|---|---|
| 1.0 MB | 15,939 × 7 | default¹ | 0.5 s | 0.4 s | 107 MB | 1.0 MB | OK — 318 duplicates removed |
| 25 MB | 390,970 × 7 | default¹ | 7.3 s | 5.2 s | 258 MB | 24.3 MB | OK — 7,801 duplicates removed |
| 100 MB | 1,551,596 × 7 | default¹ | 31.0 s | 21.6 s | 280 MB | 97.3 MB | OK — 30,955 duplicates removed |
| 100 MB | 1,551,596 × 7 | heavy²: + collapse spaces, placeholders, email lowercase, **median fill** (two passes) | 29.9 s | 34.2 s | 307 MB | 97.6 MB | OK — 379,940 cells filled |
| **500 MB (exactly 524,288,000 bytes)** | 7,698,189 × 7 | default¹ | 182 s | 107 s | **383 MB** (server process) | 486.5 MB | OK — 153,579 duplicates removed; upload over loopback 3 s |
| 500 MB + 1 byte | — | — | rejected: HTTP 413, no files left | | | | as required |

¹ default = trim, normalize column names, remove duplicates, numeric conversion, date normalization, preserve missing.
² heavy = default + collapse whitespace + placeholders as missing + lowercase emails + median fill on one column.

## What this tells us
* **Memory is flat**: ~280 MB at 100 MB input, 383 MB at 500 MB input (the growth is the 8-byte-per-distinct-row duplicate index:
  7.5 M rows ≈ 60 MB). A naive `pd.read_csv` of the whole 500 MB file as text would need several GB.
* **Throughput** (computed from the table): diagnosis ≈ 2.9–3.4 MB/s, cleaning ≈ 4.6–4.7 MB/s (single Python process; chunks are processed sequentially).
  The 500 MB file took 182 s + 107 s ≈ 5 minutes end-to-end; the UI shows measured row progress for it.
* The heavy scenario (extra treatments **and** a second pass for the median) took 58 % longer to clean than the default one (34.2 s vs 21.6 s); this was not split into per-treatment costs.

## Engine decision
Chunked pandas was kept (see ARCHITECTURE.md): bounded memory, exact cross-chunk semantics, and one code path for preview/export.
DuckDB/Polars were **not benchmarked** in this project; any claim that they would be faster is an estimate, not a measurement.
Likely next optimisation: process chunks in a process pool, or push duplicate hashing to DuckDB.

## Not measured
Wide files (> 100 columns), real 500 MB XLSX files, median on tens of millions of values, concurrent heavy jobs on one machine,
non-SSD disks. Expect XLSX (parse + convert) to be several times slower than CSV per MB.
