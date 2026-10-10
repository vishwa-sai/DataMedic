"""The repair engine. Used for BOTH the preview dry-run and the real export,
so what the user previews is what gets applied.

Order of operations (fixed and documented):
  1. trim  2. collapse spaces  3. placeholders -> missing  4. numeric text -> numbers
  5. dates -> ISO  6. email lowercase  7. remove exact duplicates (on the values after 1-6)
  8. missing-value strategy (drop rows / fill)

Large files are processed in chunks. Row-level duplicate state and the
statistics needed for mean/median/mode imputation are carried across chunk
boundaries, so results are identical to processing the file in one piece.
"""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import config, ingest, patterns as P
from .dedupe import DupTracker, row_hashes
from .errors import DataMedicError
from .writers import CsvWriter, XlsxWriter, verify_output

STAGES = [("prepare", "Preparing data"), ("treat", "Applying treatments"),
          ("fill", "Filling missing values"), ("validate", "Validating output"),
          ("report", "Generating report")]

NEEDS_STATS = ("fill_zero", "mean", "median", "mode")


def _fmt_mean(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _cap(v: str, n: int = 120) -> str:
    return v if len(v) <= n else v[: n - 1] + "…"


class Pipeline:
    def __init__(self, job_dir: Path, src: ingest.SourceInfo, analysis: dict, plan: dict, *,
                 out_path: Path | None, work_dir: Path,
                 progress: Callable[[str, int | None, int | None], None] | None = None,
                 check: Callable[[], None] | None = None,
                 preview_rows: int = 0, limit_rows: int | None = None):
        self.job_dir, self.src, self.analysis, self.plan = job_dir, src, analysis, plan
        self.out_path, self.work_dir = out_path, work_dir
        self.progress = progress or (lambda *a: None)
        self.check = check or (lambda: None)
        self.preview_rows, self.limit_rows = preview_rows, limit_rows
        self.header = src.header
        self.ncols = len(self.header)
        self.kept = plan["kept_columns"]
        self.scope = plan["missing_columns"]
        self.strategy = plan["missing_strategy"]
        self.needs_stats = self.strategy in NEEDS_STATS
        self.tracker = DupTracker()
        self.warnings: list[str] = list(plan.get("notes", []))
        for i, why in plan.get("date_skipped", {}).items():
            self.warnings.append(f"Dates in “{self._nm(i)}” were not converted: {why}.")
        # metrics
        self.rows_in = 0
        self.rows_out = 0
        self.dups_removed = 0
        self.rows_dropped_missing = 0
        self.trimmed = 0
        self.collapsed = 0
        self.ph_nulled = 0
        self.email_lower = 0
        self.col = {i: Counter() for i in range(self.ncols)}   # per-column counters
        self.email_samples: dict[int, list] = {i: [] for i in plan["email_columns"]}
        # preview
        self.shown = self.kept[: config.PREVIEW_COLS]
        self.prev: dict[int, dict] = {}
        self.prefix_rids: list[int] = []
        # stats for imputation
        self.stats: dict[int, dict] = {}
        self.fill_values: dict[int, str] = {}
        self.fill_skipped: dict[int, str] = {}
        self.numeric_typed: set[int] = set(plan["numeric_columns"])
        self.writer = None
        self.kept_rows_stage1 = 0
        self.names: list[str] = []

    def _nm(self, i: int) -> str:
        return self.header[i].strip() or f"column {i + 1}"

    # ------------------------------------------------------------------
    def out_names(self) -> list[str]:
        if self.plan["normalize_columns"]:
            full = P.normalize_headers(self.header)
            return [full[i] for i in self.kept]
        return [self.header[i] for i in self.kept]

    # ------------------------------------------------------------------
    def run(self) -> dict:
        t0 = time.monotonic()
        self.progress("prepare", None, None)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        names = self.out_names()
        self.names = names
        if self.out_path is not None:
            if self.plan["output_format"] == "csv":
                self.writer = CsvWriter(self.out_path, names, self.kept)
            else:
                self.writer = None   # created after stats are known (needs numeric set)
        inter_path = self.work_dir / "stage1.csv"
        inter = None
        completed = False
        total_rows = min(self.analysis["summary"]["rows"], self.limit_rows or 10 ** 18)
        try:
            if self.needs_stats:
                inter = open(inter_path, "w", encoding="utf-8", newline="")
            else:
                self._open_xlsx_if_needed()
            # ---------------- pass 1 ----------------
            for chunk in ingest.read_csv_chunks(self.job_dir, self.src, ingest.chunk_rows_for(self.ncols)):
                if self.limit_rows is not None:
                    if self.rows_in >= self.limit_rows:
                        break
                    chunk = chunk.iloc[: self.limit_rows - self.rows_in]
                self.check()
                df, rid = self._stage1(chunk, self.rows_in)
                self.rows_in += len(chunk)
                if self.needs_stats:
                    self._accumulate(df)
                    df[self.kept].to_csv(inter, header=False, index=False, lineterminator="\n")
                    self.kept_rows_stage1 += len(df)
                    for r in rid[rid < self.preview_rows]:
                        self.prefix_rids.append(int(r))
                else:
                    self._finish(df, rid)
                self.progress("treat", self.rows_in, total_rows)
            if inter is not None:
                inter.close()
                inter = None
            if self.needs_stats:
                self._resolve_fill_values()
                self._open_xlsx_if_needed()
                self._pass2(inter_path, total_rows)
            completed = True
        finally:
            if inter is not None:
                inter.close()
            if not completed and self.writer is not None:
                self.writer.abort()
            shutil.rmtree(self.work_dir, ignore_errors=True)
        if self.writer is not None:
            self.writer.close()
            self.warnings.extend(self.writer.warnings)
        if self.rows_in == 0:
            raise DataMedicError("No data rows were processed.", "empty_data", 422)
        if self.rows_out == 0:
            self.warnings.append("The cleaned dataset contains no data rows: every row was removed by the selected treatments.")
        return self._result(time.monotonic() - t0)

    def _open_xlsx_if_needed(self) -> None:
        if self.out_path is not None and self.plan["output_format"] == "xlsx" and self.writer is None:
            numeric = set(self.plan["numeric_columns"]) | {i for i, s in self.stats.items() if s.get("applicable")}
            dates = {i: d["with_time"] for i, d in self.plan["date_columns"].items()}
            self.numeric_typed = numeric
            self.writer = XlsxWriter(self.out_path, self.names, self.kept, numeric, dates)

    # ------------------------------------------------------------------
    def _stage1(self, chunk: pd.DataFrame, start: int):
        p = self.plan
        n = len(chunk)
        rid = np.arange(start, start + n)
        for i in range(self.ncols):
            self.col[i]["missing_before"] += int(P.is_missing(chunk[i]).sum())
        cols = {}
        for ci in self.kept:
            s = chunk[ci]
            c = self.col[ci]
            if p["trim"]:
                t = s.str.strip()
                k = int((t != s).sum())
                self.trimmed += k
                c["trimmed"] += k
                s = t
            if p["collapse_spaces"]:
                t = s.str.replace(r"\s+", " ", regex=True)
                k = int((t != s).sum())
                self.collapsed += k
                c["collapsed"] += k
                s = t
            if p["placeholders_as_missing"]:
                ph = s.str.strip().str.lower().isin(P.PLACEHOLDERS)
                k = int(ph.sum())
                if k:
                    self.ph_nulled += k
                    c["placeholders"] += k
                    s = s.mask(ph, "")
            if ci in p["numeric_columns"]:
                s = self._numeric(s, c)
            if ci in p["date_columns"]:
                s = self._dates(s, c, p["date_columns"][ci])
            if ci in p["email_columns"] and p["email_lowercase"]:
                ok = s.str.strip().str.fullmatch(P.EMAIL_RE) & (s.str.lower() != s)
                k = int(ok.sum())
                if k:
                    s = s.copy()
                    s[ok] = s[ok].str.lower()
                    self.email_lower += k
                    c["email_lowercased"] += k
            cols[ci] = s
        df = pd.DataFrame(cols)
        dup = np.zeros(n, bool)
        if p["remove_duplicates"]:
            dup = self.tracker.add(row_hashes(df))
            self.dups_removed += int(dup.sum())
        # preview capture (before / after-stage-1)
        if self.preview_rows and start < self.preview_rows:
            for j in range(min(n, self.preview_rows - start)):
                self.prev[start + j] = {
                    "before": [_cap(chunk.at[j, c]) for c in self.shown],
                    "after": None if dup[j] else [_cap(df.at[j, c]) for c in self.shown],
                    "status": "duplicate" if dup[j] else "kept"}
        if dup.any():
            df, rid = df[~dup].reset_index(drop=True), rid[~dup]
        # email validation on surviving rows
        for ci in p["email_columns"]:
            s = df[ci]
            nb = ~P.is_missing(s)
            ok = s.str.strip().str.fullmatch(P.EMAIL_RE)
            bad = nb & ~ok
            self.col[ci]["email_invalid"] += int(bad.sum())
            self.col[ci]["email_checked"] += int(nb.sum())
            for j in np.flatnonzero(bad.to_numpy())[: 10 - len(self.email_samples[ci])]:
                self.email_samples[ci].append({"row": int(rid[j]) + 1, "value": _cap(s.iat[j], 80)})
        return df, rid

    def _numeric(self, s: pd.Series, c: Counter) -> pd.Series:
        st = s.str.strip()
        nb = st.ne("")
        ph = st.str.lower().isin(P.PLACEHOLDERS)
        isnum = nb & st.str.fullmatch(P.NUM_RE)
        c["numeric_unconverted"] += int((nb & ~ph & ~isnum).sum())
        if isnum.any():
            canon = P.canonicalize_numeric(st[isnum])
            changed = canon.to_numpy() != s[isnum].to_numpy()
            c["numeric_converted"] += int(changed.sum())
            s = s.copy()
            s[isnum] = canon
        return s

    def _dates(self, s: pd.Series, c: Counter, spec: dict) -> pd.Series:
        st = s.str.strip()
        nb = st.ne("").to_numpy()
        idx = np.flatnonzero(nb)
        if len(idx) == 0:
            return s
        sub = st.iloc[idx].reset_index(drop=True)
        parsed = P.parse_dates(sub, spec["order"])
        valid = parsed.dt.notna().to_numpy()
        fmt = P.DATE_TIME_FMT if spec["with_time"] else P.DATE_ONLY_FMT
        ph = sub.str.lower().isin(P.PLACEHOLDERS).to_numpy()
        c["date_unparsed"] += int((~valid & ~ph).sum())
        if valid.any():
            iso = parsed.dt[valid].dt.strftime(fmt).to_numpy()
            target = idx[valid]
            old = s.iloc[target].to_numpy()
            c["date_normalized"] += int((iso != old).sum())
            s = s.copy()
            s.iloc[target] = iso
        return s

    # ------------------------------------------------------------------
    def _accumulate(self, df: pd.DataFrame) -> None:
        """Pass-1 statistics needed to compute fill values (on rows that survive dedupe)."""
        for ci in self.scope:
            st = self.stats.setdefault(ci, {"n": 0, "sum": 0.0, "bad": 0, "vals": [], "counter": Counter(),
                                            "overflow": False})
            vals = df[ci].str.strip()
            vals = vals[vals.ne("")]
            if self.strategy == "mode":
                if not st["overflow"]:
                    st["counter"].update(vals.value_counts().to_dict())
                    if len(st["counter"]) > config.MAX_MODE_DISTINCT:
                        st["overflow"] = True
                        st["counter"].clear()
                continue
            ok = vals.str.fullmatch(P.SIMPLE_NUM_RE)
            st["bad"] += int((~ok).sum())
            if st["bad"] == 0 and ok.any():
                nums = pd.to_numeric(vals[ok]).to_numpy(dtype=float)
                st["n"] += len(nums)
                st["sum"] += float(nums.sum())
                if self.strategy == "median":
                    st["vals"].append(nums)
                    if sum(len(v) for v in st["vals"]) > config.MAX_MEDIAN_VALUES:
                        raise DataMedicError(
                            "Median needs more memory than is safe for a dataset this large. "
                            "Use mean or mode instead.", "median_too_large", 413)

    def _resolve_fill_values(self) -> None:
        strat = self.strategy
        for ci in self.scope:
            st = self.stats.get(ci)
            if strat == "mode":
                if st is None or st["overflow"]:
                    self.fill_skipped[ci] = "too many distinct values to find the most frequent one safely"
                elif not st["counter"]:
                    self.fill_skipped[ci] = "it has no non-missing values"
                else:
                    top = max(st["counter"].items(), key=lambda kv: (kv[1], [-ord(ch) for ch in kv[0]]))
                    self.fill_values[ci] = top[0]
                continue
            if st is None or st["bad"] or st["n"] == 0:
                self.fill_skipped[ci] = ("it is not a purely numeric column (convert numbers first, or choose another strategy)"
                                         if st and st["bad"] else "it has no numeric values")
                if st is not None:
                    st["applicable"] = False
                continue
            st["applicable"] = True
            if strat == "fill_zero":
                self.fill_values[ci] = "0"
            elif strat == "mean":
                self.fill_values[ci] = _fmt_mean(st["sum"] / st["n"])
            else:
                self.fill_values[ci] = format(float(np.median(np.concatenate(st["vals"]))), ".15g")
            st["vals"] = []
        for ci, why in self.fill_skipped.items():
            self.warnings.append(f"“{self._nm(ci)}” was left unchanged by the “{strat}” strategy: {why}.")

    def _pass2(self, inter_path: Path, total_rows: int) -> None:
        pos = 0
        k = len(self.kept)
        rename = dict(enumerate(self.kept))
        reader = pd.read_csv(inter_path, header=None, dtype=str, keep_default_na=False, na_filter=False,
                             skip_blank_lines=False, chunksize=ingest.chunk_rows_for(k))
        done = 0
        for chunk in reader:
            self.check()
            df = chunk.rename(columns=rename)
            rid = np.full(len(df), -1)
            take = max(0, min(len(df), len(self.prefix_rids) - pos))
            if take:
                rid[:take] = self.prefix_rids[pos:pos + take]
            pos += len(df)
            self._finish(df, rid)
            done += len(df)
            self.progress("fill", done, self.kept_rows_stage1)
        if done != self.kept_rows_stage1:
            raise DataMedicError("Internal consistency check failed between processing passes.", "internal", 500)

    def _finish(self, df: pd.DataFrame, rid: np.ndarray) -> None:
        strat = self.strategy
        if strat == "drop_rows":
            mm = np.zeros(len(df), bool)
            for ci in self.scope:
                mm |= P.is_missing(df[ci]).to_numpy()
            if mm.any():
                self.rows_dropped_missing += int(mm.sum())
                for r in rid[mm]:
                    if int(r) in self.prev:
                        self.prev[int(r)]["status"] = "dropped_missing"
                        self.prev[int(r)]["after"] = None
                df, rid = df[~mm].reset_index(drop=True), rid[~mm]
        elif strat != "preserve":
            for ci in self.scope:
                val = self.plan["missing_text"] if strat == "fill_text" else self.fill_values.get(ci)
                if val is None:
                    continue
                miss = P.is_missing(df[ci])
                k = int(miss.sum())
                if k:
                    self.col[ci]["filled"] += k
                    df[ci] = df[ci].mask(miss, val)
        for ci in self.kept:
            self.col[ci]["missing_after"] += int(P.is_missing(df[ci]).sum())
        self.rows_out += len(df)
        if self.preview_rows and len(df):
            for j in np.flatnonzero((rid >= 0) & (rid < self.preview_rows)):
                self.prev[int(rid[j])]["after"] = [_cap(df.at[j, c]) for c in self.shown]
        if self.writer is not None:
            self.writer.write(df)

    # ------------------------------------------------------------------
    def _result(self, seconds: float) -> dict:
        p, m = self.plan, self.col
        sampled = self.limit_rows is not None and self.rows_in < self.analysis["summary"]["rows"]
        preview_rows = []
        for r in sorted(self.prev):
            e = self.prev[r]
            after = e["after"]
            e["row"] = r + 1
            e["changed"] = [] if after is None else [a != b for a, b in zip(after, e["before"])]
            preview_rows.append(e)
        columns = []
        for i in range(self.ncols):
            c = m[i]
            kept = i in self.kept
            columns.append({
                "index": i, "original_name": self.header[i],
                "output_name": self.names[self.kept.index(i)] if kept else None,
                "removed": not kept,
                "missing_before": c["missing_before"], "missing_after": c["missing_after"] if kept else None,
                "filled": c["filled"], "trimmed": c["trimmed"], "collapsed": c["collapsed"],
                "placeholders_nulled": c["placeholders"],
                "numeric_converted": c["numeric_converted"], "numeric_unconverted": c["numeric_unconverted"],
                "dates_normalized": c["date_normalized"], "dates_unparsed": c["date_unparsed"],
                "emails_invalid": c["email_invalid"], "emails_lowercased": c["email_lowercased"],
                "fill_value": (p["missing_text"] if self.strategy == "fill_text" and i in self.scope
                               else self.fill_values.get(i)),
            })
        metrics = {
            "rows_in": self.rows_in, "rows_out": self.rows_out,
            "cols_in": self.ncols, "cols_out": len(self.kept),
            "missing_before": sum(m[i]["missing_before"] for i in range(self.ncols)),
            "missing_after": sum(m[i]["missing_after"] for i in self.kept),
            "duplicates_removed": self.dups_removed,
            "rows_dropped_missing": self.rows_dropped_missing,
            "cells_trimmed": self.trimmed, "cells_collapsed": self.collapsed,
            "placeholders_nulled": self.ph_nulled,
            "missing_filled": sum(m[i]["filled"] for i in self.kept),
            "numeric_converted": sum(m[i]["numeric_converted"] for i in self.kept),
            "numeric_columns_converted": len(p["numeric_columns"]),
            "dates_normalized": sum(m[i]["date_normalized"] for i in self.kept),
            "dates_unparsed": sum(m[i]["date_unparsed"] for i in self.kept),
            "date_columns_normalized": len(p["date_columns"]),
            "emails_invalid": sum(m[i]["email_invalid"] for i in p["email_columns"]),
            "emails_lowercased": self.email_lower,
            "columns_renamed": sum(1 for i, n in zip(self.kept, self.names) if n != self.header[i]),
            "columns_removed": len(self.ncols_removed()),
            "processing_seconds": round(seconds, 3),
        }
        return {
            "metrics": metrics, "columns": columns, "operations": self._operations(metrics),
            "warnings": self.warnings,
            "email_samples": {str(k): v for k, v in self.email_samples.items() if v},
            "sampled": sampled,
            "preview": {"rows": preview_rows,
                        "columns_before": [self.header[i] for i in self.shown],
                        "columns_after": [self.names[self.kept.index(i)] for i in self.shown],
                        "total_columns_shown": len(self.shown), "total_columns": len(self.kept)},
        }

    def ncols_removed(self):
        return [i for i in range(self.ncols) if i not in self.kept]

    def _operations(self, m: dict) -> list[dict]:
        p = self.plan

        def op(key, label, requested, applied, affected, detail):
            status = "not_requested" if not requested else ("applied" if applied else "skipped")
            return {"key": key, "label": label, "requested": requested, "status": status,
                    "affected": affected, "detail": detail}

        strat = self.strategy
        miss_detail = {
            "preserve": "Missing values were left as they are.",
            "drop_rows": f"{m['rows_dropped_missing']:,} row(s) with a missing value were removed.",
            "fill_text": f"{m['missing_filled']:,} missing cell(s) filled with “{p['missing_text']}”.",
            "fill_zero": f"{m['missing_filled']:,} missing cell(s) in numeric columns filled with 0.",
            "mean": f"{m['missing_filled']:,} missing cell(s) filled with the column mean.",
            "median": f"{m['missing_filled']:,} missing cell(s) filled with the column median.",
            "mode": f"{m['missing_filled']:,} missing cell(s) filled with the most frequent value.",
        }[strat]
        miss_aff = m["rows_dropped_missing"] if strat == "drop_rows" else m["missing_filled"]
        miss_applied = strat == "drop_rows" or bool(self.fill_values) or strat == "fill_text"
        ops = [
            op("trim", "Trim whitespace", p["trim"], True, m["cells_trimmed"],
               f"{m['cells_trimmed']:,} cell(s) had leading/trailing whitespace removed."),
            op("collapse_spaces", "Collapse repeated whitespace", p["collapse_spaces"], True, m["cells_collapsed"],
               f"{m['cells_collapsed']:,} cell(s) changed."),
            op("placeholders", "Treat placeholders (N/A, null…) as missing", p["placeholders_as_missing"], True,
               m["placeholders_nulled"], f"{m['placeholders_nulled']:,} cell(s) emptied."),
            op("normalize_columns", "Normalize column names", p["normalize_columns"], True, m["columns_renamed"],
               f"{m['columns_renamed']} of {m['cols_out']} column name(s) changed."),
            op("drop_empty_columns", "Remove empty columns", p["drop_empty_columns"], True, m["columns_removed"],
               f"{m['columns_removed']} empty column(s) removed."),
            op("numeric", "Convert numeric text to numbers", p["numeric_requested"],
               bool(p["numeric_columns"]), m["numeric_converted"],
               f"{m['numeric_converted']:,} value(s) reformatted in {len(p['numeric_columns'])} column(s)."),
            op("dates", "Normalize dates to ISO 8601", p["dates_requested"],
               bool(p["date_columns"]), m["dates_normalized"],
               f"{m['dates_normalized']:,} value(s) rewritten in {len(p['date_columns'])} column(s); "
               f"{m['dates_unparsed']:,} left unchanged because they could not be parsed."
               + (f" {len(p['date_skipped'])} column(s) skipped (ambiguous)." if p["date_skipped"] else "")),
            op("emails", "Validate email addresses", bool(p["email_columns"]), True, m["emails_invalid"],
               f"{m['emails_invalid']:,} invalid address(es) found (reported, not changed)."),
            op("email_lowercase", "Lowercase valid emails", p["email_lowercase"] and bool(p["email_columns"]), True,
               m["emails_lowercased"], f"{m['emails_lowercased']:,} address(es) lowercased."),
            op("remove_duplicates", "Remove exact duplicate rows", p["remove_duplicates"], True, m["duplicates_removed"],
               f"{m['duplicates_removed']:,} duplicate row(s) removed (first occurrence kept)."),
            op("missing", f"Missing values: {strat.replace('_', ' ')}", strat != "preserve", miss_applied, miss_aff,
               miss_detail + (f" {len(self.fill_skipped)} column(s) skipped." if self.fill_skipped else "")),
        ]
        return ops


