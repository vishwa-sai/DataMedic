"""Streaming diagnosis. One pass over the file in chunks; every number reported
is counted from the data. Nothing here modifies the file."""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import config, ingest, patterns as P
from .dedupe import DupTracker, row_hashes
from .errors import DataMedicError


class ColAcc:
    """Accumulates per-column statistics across chunks."""

    def __init__(self, idx: int, header: str):
        self.idx, self.header = idx, header
        self.n = self.blank = self.ws_only = self.edge_ws = self.inner_ws = 0
        self.placeholders = 0
        self.ph_examples: list[str] = []
        self.core_n = 0
        self.num_like = self.num_clean = self.lead_zero = self.long_digits = 0
        self.nonnum: list[str] = []
        self.distinct: set[str] = set()
        self.examples: list[str] = []
        self.upper_emails = 0
        self.date_on: bool | None = None
        self.email_on: bool | None = None
        # dates
        self.d_matched = self.d_time = self.d_xyz = self.d_ev_dmy = self.d_ev_mdy = 0
        self.d_valid_nonxyz = self.d_norm_nonxyz = self.d_xyz_dmy = self.d_xyz_mdy = 0
        self.d_classes: Counter = Counter()
        self.d_bad: list[str] = []
        # emails
        self.e_valid = self.e_invalid = 0
        self.e_bad: list[tuple[int, str]] = []

    # ------------------------------------------------------------------
    def update(self, s: pd.Series, start: int, st: pd.Series) -> None:
        """`s` raw chunk column, `st` its stripped version, `start` 0-based row offset."""
        blank = st.eq("")
        nb = ~blank
        nbn = int(nb.sum())
        self.n += len(s)
        self.blank += len(s) - nbn
        self.ws_only += int((blank & s.ne("")).sum())
        if nbn == 0:
            return
        self.edge_ws += int((nb & s.ne(st)).sum())
        vals = st[nb]
        self.inner_ws += int(vals.str.contains(r"\s{2,}", regex=True).sum())
        ph = vals.str.lower().isin(P.PLACEHOLDERS)
        nph = int(ph.sum())
        self.placeholders += nph
        if nph and len(self.ph_examples) < 3:
            for v in vals[ph].unique()[:3]:
                if v not in self.ph_examples and len(self.ph_examples) < 3:
                    self.ph_examples.append(v)
        core = vals[~ph]
        self.core_n += len(core)
        if len(core) == 0:
            return
        if len(self.distinct) < 3:
            self.distinct.update(core.unique()[:3].tolist())
        if len(self.examples) < 3:
            for v in core.unique()[:3]:
                if v not in self.examples and len(self.examples) < 3:
                    self.examples.append(v[:60])

        # ---- numeric ----------------------------------------------------
        isnum = core.str.fullmatch(P.NUM_RE)
        nnum = int(isnum.sum())
        self.num_like += nnum
        if nnum < len(core) and len(self.nonnum) < 5:
            for v in core[~isnum].unique()[:5]:
                if v not in self.nonnum and len(self.nonnum) < 5:
                    self.nonnum.append(v[:60])
        if nnum:
            nv = core[isnum]
            self.num_clean += int((P.canonicalize_numeric(nv) != nv).sum())
            self.lead_zero += int(nv.str.match(P.LEAD_ZERO_RE).sum())
            self.long_digits += int((nv.str.count(r"[0-9]") > 15).sum())

        # ---- decide (once) whether date / email checks are worthwhile ----
        if self.date_on is None or self.email_on is None:
            sample = core.head(3000)
            self.date_on = bool(sample.str.contains(P.DATE_LOOSE_RE).mean() >= 0.3)
            self.email_on = bool(P.is_email_name(self.header) or sample.str.contains("@", regex=False).mean() >= 0.3)

        if self.date_on and nnum < len(core) * 0.5:
            self._update_dates(core)
        if self.email_on:
            ok = core.str.fullmatch(P.EMAIL_RE)
            self.e_valid += int(ok.sum())
            bad = core[~ok]
            self.e_invalid += len(bad)
            for i, v in bad.head(10).items():
                if len(self.e_bad) < 10:
                    self.e_bad.append((start + int(i) + 1, v[:80]))
            self.upper_emails += int(core[ok].str.contains(r"[A-Z]", regex=True).sum())

    def _update_dates(self, core: pd.Series) -> None:
        core = core.reset_index(drop=True)
        a = P.parse_dates(core, "dmy")
        b = P.parse_dates(core, "mdy")
        self.d_matched += int(a.matched.sum())
        self.d_time += int(a.has_time.sum())
        xyz = a.cls == "xyz"
        self.d_xyz += int(xyz.sum())
        self.d_ev_dmy += int(a.ev_dmy.sum())
        self.d_ev_mdy += int(a.ev_mdy.sum())
        self.d_classes.update(Counter(a.cls[a.matched].tolist()))
        valid_a = a.dt.notna().to_numpy()
        valid_b = b.dt.notna().to_numpy()
        self.d_xyz_dmy += int((valid_a & xyz).sum())
        self.d_xyz_mdy += int((valid_b & xyz).sum())
        nx = valid_a & ~xyz
        self.d_valid_nonxyz += int(nx.sum())
        if nx.any():
            sub = a.dt[nx]
            iso = np.where(a.has_time[nx], sub.dt.strftime(P.DATE_TIME_FMT), sub.dt.strftime(P.DATE_ONLY_FMT))
            self.d_norm_nonxyz += int((iso != core[nx].to_numpy()).sum())
        if len(self.d_bad) < 5:
            bad_mask = ~(valid_a | valid_b) & ~xyz | (xyz & ~valid_a & ~valid_b)
            for v in core[bad_mask].unique()[:5]:
                if v not in self.d_bad and len(self.d_bad) < 5:
                    self.d_bad.append(v[:40])

    # ------------------------------------------------------------------
    def finalize(self) -> dict:
        n_core = self.core_n
        c: dict = {
            "index": self.idx, "name": self.header,
            "rows": self.n, "missing": self.blank,
            "missing_pct": round(100 * self.blank / self.n, 2) if self.n else 0.0,
            "whitespace_edge": self.edge_ws, "whitespace_inner": self.inner_ws,
            "whitespace_only": self.ws_only,
            "placeholders": self.placeholders, "placeholder_examples": self.ph_examples,
            "examples": self.examples,
        }
        empty = n_core == 0
        c["empty"] = bool(empty and self.placeholders == 0)
        c["constant"] = bool(n_core > 0 and len(self.distinct) == 1 and self.blank == 0 and self.placeholders == 0)

        # numeric
        id_name = P.is_identifier_name(self.header)
        all_numeric = n_core > 0 and self.num_like == n_core
        reason = None
        if all_numeric and id_name:
            reason = "its name suggests an identifier/code"
        elif all_numeric and self.lead_zero:
            reason = "some values have leading zeros"
        elif all_numeric and self.long_digits:
            reason = "some values have more than 15 digits"
        c["numeric"] = {
            "all_numeric": bool(all_numeric),
            "candidate": bool(all_numeric and reason is None),
            "needs_cleaning": int(self.num_clean) if all_numeric and reason is None else 0,
            "protected_reason": reason if all_numeric else None,
            "numeric_like": int(self.num_like),
            "non_numeric_samples": self.nonnum,
            "mixed": bool(n_core > 0 and 0 < self.num_like < n_core and self.num_like / n_core >= 0.5),
        }

        # dates
        order = P.resolve_date_order(self.d_ev_dmy, self.d_ev_mdy, self.d_xyz)
        if order == "dmy":
            xyz_valid = self.d_xyz_dmy
        elif order == "mdy":
            xyz_valid = self.d_xyz_mdy
        else:
            xyz_valid = max(self.d_xyz_dmy, self.d_xyz_mdy)
        valid_total = self.d_valid_nonxyz + xyz_valid
        d_candidate = bool(n_core > 0 and valid_total >= 1 and valid_total / n_core >= 0.9
                           and self.num_like < n_core * 0.5)
        needs_norm = self.d_norm_nonxyz + (xyz_valid if order in ("dmy", "mdy") else 0)
        c["date"] = {
            "candidate": d_candidate, "order": order if d_candidate else None,
            "with_time": bool(self.d_time > 0),
            "formats": {P.DATE_CLASS_LABELS[k]: v for k, v in self.d_classes.items()} if d_candidate else {},
            "needs_normalization": int(needs_norm) if d_candidate else 0,
            "ambiguous_values": int(self.d_xyz) if d_candidate and order in ("ambiguous", "conflict") else 0,
            "unparsed": int(n_core - valid_total) if d_candidate else 0,
            "unparsed_samples": self.d_bad if d_candidate else [],
            "partially_dates": bool(not d_candidate and n_core > 0 and self.d_matched / n_core >= 0.3),
            "date_like": int(self.d_matched),
        }

        # emails
        tot = self.e_valid + self.e_invalid
        is_email = bool(self.email_on and tot > 0 and (P.is_email_name(self.header) or self.e_valid / tot >= 0.6))
        c["email"] = {
            "is_email": is_email, "invalid": int(self.e_invalid) if is_email else 0,
            "valid": int(self.e_valid) if is_email else 0,
            "samples": [{"row": r, "value": v} for r, v in self.e_bad] if is_email else [],
            "uppercase": int(self.upper_emails) if is_email else 0,
        }

        if c["empty"]:
            t = "empty"
        elif is_email:
            t = "email"
        elif d_candidate:
            t = "date"
        elif c["numeric"]["candidate"]:
            t = "numeric"
        elif all_numeric:
            t = "text (numeric-looking, protected)"
        elif c["numeric"]["mixed"]:
            t = "mixed"
        else:
            t = "text"
        c["inferred_type"] = t
        return c


def _pl(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _issue(id_, severity, category, title, detail, why, treatment, key=None, columns=None,
           count=None, samples=None):
    return {"id": id_, "severity": severity, "category": category, "title": title, "detail": detail,
            "why": why, "treatment": treatment, "treatment_key": key, "columns": columns or [],
            "count": count, "samples": samples or []}


def analyze(job_dir: Path, src: ingest.SourceInfo, file_meta: dict,
            progress: Callable[[int], None] | None = None,
            check: Callable[[], None] | None = None) -> dict:
    t0 = time.monotonic()
    header = src.header
    ncols = len(header)
    ingest.check_width(ncols)
    accs = [ColAcc(i, h) for i, h in enumerate(header)]
    dup_raw, dup_trim = DupTracker(), DupTracker()
    n_dup_raw = n_dup_trim = 0
    dup_samples: list[int] = []
    empty_rows = 0
    total = 0
    for chunk in ingest.read_csv_chunks(job_dir, src, ingest.chunk_rows_for(ncols)):
        if check:
            check()
        stripped = {}
        allblank = np.ones(len(chunk), bool)
        for i in range(ncols):
            s = chunk[i]
            st = s.str.strip()
            stripped[i] = st
            allblank &= st.eq("").to_numpy()
            accs[i].update(s, total, st)
        empty_rows += int(allblank.sum())
        dmask = dup_raw.add(row_hashes(chunk))
        n_dup_raw += int(dmask.sum())
        if len(dup_samples) < 10 and dmask.any():
            dup_samples += [int(total + i + 1) for i in np.flatnonzero(dmask)[: 10 - len(dup_samples)]]
        n_dup_trim += int(dup_trim.add(row_hashes(pd.DataFrame(stripped))).sum())
        total += len(chunk)
        if progress:
            progress(total)
    if total == 0:
        raise DataMedicError("The file has a header row but no data rows.", "empty_data", 422)

    cols = [a.finalize() for a in accs]
    normalized = P.normalize_headers(header)
    lowered = [h.strip().casefold() for h in header]
    dupe_names = {n for n, k in Counter(lowered).items() if k > 1 and n}
    for c, nn in zip(cols, normalized):
        h = header[c["index"]]
        c["normalized_name"] = nn
        c["name_blank"] = h.strip() == ""
        c["name_duplicate"] = h.strip().casefold() in dupe_names and h.strip() != ""
        c["name_needs_normalization"] = (nn != h)

    cells = total * ncols
    missing_cells = sum(c["missing"] for c in cols)
    summary = {
        "rows": total, "columns": ncols, "cells": cells, "missing_cells": missing_cells,
        "missing_pct": round(100 * missing_cells / cells, 3) if cells else 0.0,
        "duplicate_rows": n_dup_raw, "duplicate_rows_after_trim": n_dup_trim,
        "duplicate_samples": dup_samples, "empty_rows": empty_rows,
        "empty_columns": [c["index"] for c in cols if c["empty"]],
        "scan_seconds": round(time.monotonic() - t0, 2),
    }
    analysis = {
        "file": file_meta, "summary": summary, "columns": cols,
        "issues": build_issues(cols, summary, src, header),
    }
    analysis["issue_counts"] = dict(Counter(i["severity"] for i in analysis["issues"]))
    return analysis


def build_issues(cols: list[dict], s: dict, src: ingest.SourceInfo, header: list[str]) -> list[dict]:
    out: list[dict] = []
    nm = lambda c: c["name"] if c["name"].strip() else f"(column {c['index'] + 1})"
    top = lambda items, k=4: ", ".join(items[:k]) + (f" and {len(items) - k} more" if len(items) > k else "")

    for w in src.warnings:
        out.append(_issue("file-warning", "likely", "File", "File reading note", w,
                          "Affects how reliably the data was read.", "No treatment needed; just be aware."))

    blank = [c for c in cols if c["name_blank"]]
    if blank:
        out.append(_issue("hdr-blank", "confirmed", "Column names", _pl(len(blank), "blank column name"),
                          "Columns without a header: " + top([f"column {c['index'] + 1}" for c in blank]),
                          "Unnamed columns cannot be referenced reliably in other tools.",
                          "Normalize column names (blank names become column_N).", "normalize_columns",
                          [c["index"] for c in blank], len(blank)))
    dups = [c for c in cols if c["name_duplicate"]]
    if dups:
        out.append(_issue("hdr-dup", "confirmed", "Column names", f"{len(dups)} columns share a name",
                          "Duplicated names: " + top(sorted({c["name"] for c in dups})),
                          "Duplicate names make columns ambiguous and break many tools.",
                          "Normalize column names (duplicates get _2, _3 suffixes).", "normalize_columns",
                          [c["index"] for c in dups], len(dups)))
    messy = [c for c in cols if c["name_needs_normalization"] and not c["name_blank"] and not c["name_duplicate"]]
    if messy:
        out.append(_issue("hdr-style", "suggestion", "Column names", f"{len(messy)} column names are not in a consistent style",
                          "e.g. " + top([f"“{c['name']}” → {c['normalized_name']}" for c in messy], 3),
                          "Spaces, capitals and symbols in names cause friction in code and databases.",
                          "Normalize column names to lowercase snake_case.", "normalize_columns",
                          [c["index"] for c in messy], len(messy)))

    head_numeric = [h for h in header if h.strip() and P.NUM_RE.fullmatch(h.strip())]
    if header and len(head_numeric) == len([h for h in header if h.strip()]) and head_numeric:
        out.append(_issue("hdr-data", "likely", "Column names", "The first row looks like data, not headers",
                          "Every non-blank header cell is a number.",
                          "DataMedic always treats the first row as the header.",
                          "Add a header row to the file and re-upload if this is wrong."))

    if s["duplicate_rows"]:
        out.append(_issue("dup-rows", "confirmed", "Duplicates", _pl(s["duplicate_rows"], "exact duplicate row"),
                          f"Rows identical to an earlier row in every column (first at data row {s['duplicate_samples'][0]}).",
                          "Duplicates inflate counts and totals.",
                          "Remove duplicates, keeping the first occurrence.", "remove_duplicates", [], s["duplicate_rows"],
                          [f"row {r}" for r in s["duplicate_samples"][:5]]))
    extra = s["duplicate_rows_after_trim"] - s["duplicate_rows"]
    if extra > 0:
        out.append(_issue("dup-trim", "likely", "Duplicates", f"{_pl(extra, 'more duplicate')} appear after trimming whitespace",
                          "These rows differ only by leading/trailing spaces.",
                          "Whitespace differences hide real duplicates.",
                          "Enable “Trim whitespace” together with duplicate removal.", "trim", [], extra))
    if s["empty_rows"]:
        out.append(_issue("empty-rows", "likely", "Missing values", _pl(s["empty_rows"], "completely blank row"),
                          "Rows where every cell is empty.", "They carry no information.",
                          "Choose “Drop rows containing missing values” (this also drops partially-empty rows) "
                          "or remove duplicates (keeps one blank row).", "missing", [], s["empty_rows"]))

    if s["missing_cells"]:
        worst = sorted([c for c in cols if c["missing"]], key=lambda c: -c["missing"])
        out.append(_issue("missing", "confirmed", "Missing values", f"{_pl(s['missing_cells'], 'missing cell')} ({s['missing_pct']}%)",
                          "Most affected: " + top([f"{nm(c)} ({c['missing']:,})" for c in worst]),
                          "Missing values break calculations and joins.",
                          "Choose a missing-value strategy: preserve, drop rows, fill with text/0, or mean/median/mode.",
                          "missing", [c["index"] for c in worst], s["missing_cells"]))
    edge = [c for c in cols if c["whitespace_edge"]]
    if edge:
        n = sum(c["whitespace_edge"] for c in edge)
        out.append(_issue("ws-edge", "confirmed", "Whitespace", f"{_pl(n, 'cell')} with leading/trailing spaces",
                          "In: " + top([nm(c) for c in edge]),
                          "“Paris” and “Paris ” are different values to a computer.",
                          "Trim whitespace.", "trim", [c["index"] for c in edge], n))
    inner = [c for c in cols if c["whitespace_inner"]]
    if inner:
        n = sum(c["whitespace_inner"] for c in inner)
        out.append(_issue("ws-inner", "likely", "Whitespace", f"{_pl(n, 'cell')} with repeated spaces/tabs inside the text",
                          "In: " + top([nm(c) for c in inner]),
                          "Double spaces usually are typing slips, but can be intentional (e.g. formatted text).",
                          "Collapse repeated whitespace (optional).", "collapse_spaces", [c["index"] for c in inner], n))
    ph = [c for c in cols if c["placeholders"]]
    if ph:
        n = sum(c["placeholders"] for c in ph)
        ex = sorted({e for c in ph for e in c["placeholder_examples"]})
        out.append(_issue("placeholders", "likely", "Missing values", f"{_pl(n, 'cell')} holding placeholder text instead of being empty",
                          "Values such as " + ", ".join(f"“{e}”" for e in ex[:5]) + " in " + top([nm(c) for c in ph]),
                          "They look like missing data, but could be real values (e.g. “NA” = Namibia).",
                          "Optionally treat placeholders as missing (off by default).", "placeholders_as_missing",
                          [c["index"] for c in ph], n))

    num = [c for c in cols if c["numeric"]["candidate"] and c["numeric"]["needs_cleaning"]]
    if num:
        n = sum(c["numeric"]["needs_cleaning"] for c in num)
        out.append(_issue("numeric", "confirmed", "Data types", f"{len(num)} column(s) hold numbers stored as formatted text",
                          "Columns: " + top([nm(c) for c in num]) + ". Every value is numeric, but uses currency symbols, thousands separators or a leading “+”.",
                          "Formatted text cannot be summed or sorted numerically.",
                          "Convert to plain numbers (e.g. “$1,200.50” → 1200.50).", "numeric", [c["index"] for c in num], n))
    prot = [c for c in cols if c["numeric"]["protected_reason"]]
    if prot:
        out.append(_issue("numeric-protected", "suggestion", "Data types",
                          f"{len(prot)} numeric-looking column(s) were deliberately left as text",
                          "; ".join(f"{nm(c)}: {c['numeric']['protected_reason']}" for c in prot[:4]),
                          "Identifiers, phone numbers and postal codes must keep their exact digits (and leading zeros).",
                          "No change will be made to these columns.", None, [c["index"] for c in prot]))
    mixed = [c for c in cols if c["numeric"]["mixed"]]
    for c in mixed:
        out.append(_issue(f"mixed-{c['index']}", "likely", "Data types", f"“{nm(c)}” mixes numbers and text",
                          f"{c['numeric']['numeric_like']:,} numeric-looking values; non-numeric examples: "
                          + ", ".join(f"“{v}”" for v in c["numeric"]["non_numeric_samples"][:4]),
                          "A column that is mostly numeric but has stray text cannot be converted safely.",
                          "Not converted automatically. Fix or blank the stray values in the source, or treat "
                          "placeholders as missing.", "placeholders_as_missing", [c["index"]],
                          c["rows"] - c["missing"] - c["numeric"]["numeric_like"]))

    for c in cols:
        d = c["date"]
        if d["candidate"] and d["needs_normalization"]:
            fm = "; ".join(f"{k} ({v:,})" for k, v in d["formats"].items())
            out.append(_issue(f"date-{c['index']}", "confirmed", "Dates", f"“{nm(c)}” contains dates in {len(d['formats'])} format(s)",
                              f"Formats found: {fm}. {d['needs_normalization']:,} value(s) will change."
                                                            + (" The column contains times, so every value is written as YYYY-MM-DD HH:MM:SS (dates without a time get 00:00:00)." if d["with_time"] else ""),
                              "Mixed date formats cannot be sorted or compared reliably.",
                              "Normalize to ISO 8601 (YYYY-MM-DD).", "dates", [c["index"]], d["needs_normalization"]))
        if d["candidate"] and d["order"] == "ambiguous":
            out.append(_issue(f"date-amb-{c['index']}", "likely", "Dates", f"“{nm(c)}”: day/month order is ambiguous",
                              "Every value like 03/04/2023 could be 3 April or 4 March; nothing in the column settles it.",
                              "Guessing wrongly would silently corrupt dates.",
                              "Choose “Day first” or “Month first” in the date treatment; otherwise this column is skipped.",
                              "dates", [c["index"]], d["ambiguous_values"]))
        if d["candidate"] and d["order"] == "conflict":
            out.append(_issue(f"date-conf-{c['index']}", "likely", "Dates", f"“{nm(c)}” mixes day-first and month-first dates",
                              "Some values can only be DD/MM and others only MM/DD.",
                              "There is no safe way to interpret the whole column.",
                              "This column will be skipped; fix it in the source.", "dates", [c["index"]], d["ambiguous_values"]))
        if d["candidate"] and d["unparsed"]:
            out.append(_issue(f"date-bad-{c['index']}", "likely", "Dates", f"“{nm(c)}” has {d['unparsed']:,} unparseable date value(s)",
                              "Examples: " + ", ".join(f"“{v}”" for v in d["unparsed_samples"][:4]),
                              "Invalid dates (e.g. 31/02/2023) or odd formats are left unchanged.",
                              "These values are NOT modified; review them manually.", None, [c["index"]], d["unparsed"]))
        if d["partially_dates"]:
            out.append(_issue(f"date-part-{c['index']}", "suggestion", "Dates", f"“{nm(c)}” may contain dates among other text",
                              f"{d['date_like']:,} value(s) look like dates but too many others do not.",
                              "Columns that are not predominantly dates are never converted.", "No change will be made.",
                              None, [c["index"]]))
        e = c["email"]
        if e["is_email"] and e["invalid"]:
            out.append(_issue(f"email-{c['index']}", "confirmed", "Emails", f"“{nm(c)}” has {e['invalid']:,} invalid email address(es)",
                              "Examples: " + ", ".join(f"“{x['value']}” (row {x['row']})" for x in e["samples"][:3]),
                              "Invalid addresses bounce and pollute mailing lists.",
                              "Invalid emails are reported (never deleted automatically).", "emails", [c["index"]], e["invalid"],
                              [f"row {x['row']}: {x['value']}" for x in e["samples"][:5]]))
        if e["is_email"] and e["uppercase"]:
            out.append(_issue(f"email-case-{c['index']}", "suggestion", "Emails", f"“{nm(c)}” has {e['uppercase']:,} email(s) with capital letters",
                              "Addresses are effectively case-insensitive.", "Different capitalisation hides duplicates.",
                              "Optionally lowercase valid emails.", "email_lowercase", [c["index"]], e["uppercase"]))

    empt = [c for c in cols if c["empty"]]
    if empt:
        out.append(_issue("empty-cols", "confirmed", "Structure", _pl(len(empt), "completely empty column"),
                          "Columns: " + top([nm(c) for c in empt]), "They add width but no information.",
                          "Optionally remove empty columns (off by default).", "drop_empty_columns",
                          [c["index"] for c in empt], len(empt)))
    const = [c for c in cols if c["constant"]]
    if const:
        out.append(_issue("const-cols", "suggestion", "Structure", f"{len(const)} column(s) contain a single repeated value",
                          "Columns: " + top([nm(c) for c in const]), "They may be redundant for analysis.",
                          "Informational only; nothing is removed.", None, [c["index"] for c in const]))
    order = {"confirmed": 0, "likely": 1, "suggestion": 2}
    out.sort(key=lambda i: order[i["severity"]])
    return out
