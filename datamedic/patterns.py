"""Shared detection / parsing helpers used by BOTH the analyzer and the
cleaning pipeline, so diagnosis and repair can never disagree."""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

PLACEHOLDERS = frozenset({
    "n/a", "na", "n.a.", "null", "none", "nan", "nil", "undefined", "-", "--", "?",
    "#n/a", "#div/0!", "#ref!", "#value!", "#name?", "#null!", "#num!",
})

CUR = "$€£¥₹"
_BODY = r"(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
NUM_RE = re.compile(rf"[+-]?[{CUR}]?\s?{_BODY}")
SIMPLE_NUM_RE = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
LEAD_ZERO_RE = re.compile(rf"[+-]?[{CUR}]?\s?0[0-9]")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")

_ID_TOKENS = frozenset({
    "id", "uuid", "guid", "zip", "zipcode", "postal", "postcode", "pin", "phone", "mobile",
    "tel", "telephone", "fax", "ssn", "sku", "code", "account", "acct", "isbn", "upc", "ean",
    "barcode", "aadhaar", "aadhar", "pan", "passport", "licence", "license", "number", "no",
    "ref", "reference", "serial", "ticket", "invoice", "order",
})


def name_tokens(name: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))
    return re.findall(r"[a-z]+", spaced.lower())


def is_identifier_name(name: str) -> bool:
    return any(t in _ID_TOKENS for t in name_tokens(name))


def is_email_name(name: str) -> bool:
    toks = name_tokens(name)
    return "email" in toks or "emails" in toks or "mail" in toks


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    return text


def normalize_headers(names: list[str]) -> list[str]:
    """snake_case, ASCII, unique, never blank. Shared by analysis and repair."""
    used: set[str] = set()
    out = []
    for i, name in enumerate(names):
        base = normalize_name(name) or f"column_{i + 1}"
        cand, k = base, 2
        while cand in used:
            cand = f"{base}_{k}"
            k += 1
        used.add(cand)
        out.append(cand)
    return out


def canonicalize_numeric(s: pd.Series) -> pd.Series:
    """Canonical plain-number text for values that already match NUM_RE."""
    t = s.str.replace(rf"[{CUR},\s]", "", regex=True)
    t = t.str.replace(r"^\+", "", regex=True)
    t = t.str.replace(r"^(-?)\.", r"\g<1>0.", regex=True)
    return t


def is_missing(s: pd.Series) -> pd.Series:
    """The single definition of 'missing': empty or whitespace-only."""
    return s.str.strip().eq("")


# ---- Dates ---------------------------------------------------------------
_MON = (r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
        r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MON_NUM = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

DATE_LOOSE_RE = re.compile(
    r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|[A-Za-z]{3,9}\.? \d{1,2}(?:st|nd|rd|th)?,? \d{4}"
    r"|\d{1,2}[ \-][A-Za-z]{3,9}[ ,\-]*\d{4}")

DATE_CLASSES: list[tuple[str, re.Pattern, str]] = [
    ("ymd_time", re.compile(
        r"^(?P<y>[0-9]{4})-(?P<m>[0-9]{2})-(?P<d>[0-9]{2})[T ](?P<H>[0-9]{2}):(?P<M>[0-9]{2})"
        r"(?::(?P<S>[0-9]{2})(?:\.[0-9]+)?)?Z?$"), "YYYY-MM-DD HH:MM[:SS]"),
    ("ymd", re.compile(r"^(?P<y>[0-9]{4})[-/.](?P<m>[0-9]{1,2})[-/.](?P<d>[0-9]{1,2})$"),
     "YYYY-MM-DD"),
    ("xyz", re.compile(r"^(?P<a>[0-9]{1,2})[-/.](?P<b>[0-9]{1,2})[-/.](?P<y>[0-9]{4})$"),
     "DD/MM/YYYY or MM/DD/YYYY"),
    ("d_mon_y", re.compile(rf"^(?P<d>[0-9]{{1,2}})[ \-]{_MON}\.?[ ,\-]*(?P<y>[0-9]{{4}})$", re.I),
     "DD Mon YYYY"),
    ("mon_d_y", re.compile(rf"^{_MON}\.? (?P<d>[0-9]{{1,2}})(?:st|nd|rd|th)?,? (?P<y>[0-9]{{4}})$", re.I),
     "Mon DD, YYYY"),
]
DATE_CLASS_LABELS = {name: label for name, _, label in DATE_CLASSES}
DATE_ONLY_FMT = "%Y-%m-%d"
DATE_TIME_FMT = "%Y-%m-%d %H:%M:%S"


class DateParse:
    __slots__ = ("dt", "matched", "has_time", "cls", "ev_dmy", "ev_mdy")

    def __init__(self, n: int):
        self.dt = pd.Series(pd.NaT, index=range(n), dtype="datetime64[ns]")
        self.matched = np.zeros(n, bool)
        self.has_time = np.zeros(n, bool)
        self.cls = np.full(n, "", dtype=object)
        self.ev_dmy = np.zeros(n, bool)   # value can only be day-first
        self.ev_mdy = np.zeros(n, bool)   # value can only be month-first


def parse_dates(st: pd.Series, order: str | None) -> DateParse:
    """Parse stripped strings. `order` resolves DD/MM vs MM/DD for the 'xyz' class
    ('dmy' | 'mdy' | None). With None, 'xyz' values stay unparsed (but evidence
    about the order is still reported)."""
    n = len(st)
    res = DateParse(n)
    if n == 0:
        return res
    year = np.full(n, np.nan); month = np.full(n, np.nan); day = np.full(n, np.nan)
    hour = np.zeros(n); minute = np.zeros(n); second = np.zeros(n)
    remaining = np.ones(n, bool)
    for name, rx, _ in DATE_CLASSES:
        if not remaining.any():
            break
        idx = np.flatnonzero(remaining)
        ex = st.iloc[idx].str.extract(rx)
        ok = ex.iloc[:, 0].notna().to_numpy()
        if not ok.any():
            continue
        ex = ex[ok]
        ii = idx[ok]
        remaining[ii] = False
        res.matched[ii] = True
        res.cls[ii] = name
        num = lambda col: pd.to_numeric(ex[col], errors="coerce").to_numpy(dtype=float)
        year[ii] = num("y")
        if name in ("ymd", "ymd_time"):
            month[ii] = num("m"); day[ii] = num("d")
            if name == "ymd_time":
                hour[ii] = num("H"); minute[ii] = num("M")
                second[ii] = np.nan_to_num(num("S"))
                res.has_time[ii] = True
        elif name == "xyz":
            a, b = num("a"), num("b")
            res.ev_dmy[ii] = (a > 12) & (b <= 12)
            res.ev_mdy[ii] = (b > 12) & (a <= 12)
            if order == "dmy":
                day[ii], month[ii] = a, b
            elif order == "mdy":
                month[ii], day[ii] = a, b
        else:
            month[ii] = ex["mon"].str.lower().str[:3].map(_MON_NUM).to_numpy(dtype=float)
            day[ii] = num("d")
    ok = ~np.isnan(year) & ~np.isnan(month) & ~np.isnan(day)
    if ok.any():
        frame = pd.DataFrame({
            "year": year[ok], "month": month[ok], "day": day[ok],
            "hour": hour[ok], "minute": minute[ok], "second": second[ok]})
        parsed = pd.to_datetime(frame, errors="coerce").to_numpy()
        full = np.full(n, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
        full[ok] = parsed.astype("datetime64[ns]")
        res.dt = pd.Series(full)
    return res


def resolve_date_order(ev_dmy: int, ev_mdy: int, xyz_total: int) -> str | None:
    """'dmy' | 'mdy' | 'conflict' | 'ambiguous' | None (no DD/MM-style values)."""
    if xyz_total == 0:
        return None
    if ev_dmy and ev_mdy:
        return "conflict"
    if ev_dmy:
        return "dmy"
    if ev_mdy:
        return "mdy"
    return "ambiguous"
