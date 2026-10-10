"""Generate a deterministic, messy synthetic CSV of (approximately or exactly) N bytes.

    python scripts/make_large_csv.py big100.csv --mb 100
    python scripts/make_large_csv.py big500.csv --exact-bytes 524288000   # exactly 500 MB
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

HEADER = "Customer ID,Full Name,Email,Amount ($),Signup Date,City,Notes\n"
CITIES = ["Paris", "London ", " Berlin", "Madrid", "Rome", "Vienna", "Oslo", "  Lisbon"]
NAMES = ["Alice Johnson", "Bob  Smith", " Carol White", "David Brown", "Eve Black ", "Frank Green"]


def rows(n_start: int, count: int, rnd: random.Random):
    out, prev = [], None
    for i in range(n_start, n_start + count):
        if prev is not None and i % 50 == 0:
            out.append(prev)                     # ~2% exact duplicates
            continue
        r = rnd.random()
        email = f"user{i}@example.com" if r > 0.08 else ("BAD_EMAIL" if r > 0.04 else "")
        amt = rnd.choice([f"${rnd.randint(1, 9999):,}.50", str(rnd.randint(1, 9999)), "", f"{rnd.randint(1, 99)}.25"])
        if "," in amt:
            amt = f'"{amt}"'
        day, mon, year = rnd.randint(1, 28), rnd.randint(1, 12), rnd.randint(2015, 2024)
        date = rnd.choice([f"{year}-{mon:02d}-{day:02d}", f"{day:02d}/{mon:02d}/{year}", f"{day + (0 if day > 12 else 0)}/{mon}/{year}", ""])
        notes = "" if rnd.random() < 0.2 else rnd.choice(["VIP", "late payer", "N/A", "ok"])
        line = f"{i:07d},{rnd.choice(NAMES)},{email},{amt},{date},{rnd.choice(CITIES)},{notes}\n"
        prev = line
        out.append(line)
    return "".join(out)


def generate(path: Path, target_bytes: int, exact: bool = False) -> dict:
    rnd = random.Random(42)
    written, n = 0, 0
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER)
        written = len(HEADER.encode())
        while written + 600 < target_bytes:
            count = max(1, min(20000, (target_bytes - written - 600) // 130))
            block = rows(n, count, rnd)
            b = len(block.encode())
            f.write(block)
            written += b
            n += count
        if exact:
            tail_prefix = "9999999,Tail Row,tail@example.com,1,2024-01-01,Paris,"
            pad = target_bytes - written - len(tail_prefix) - 1
            assert pad >= 0, "target too small for exact padding"
            f.write(tail_prefix + "p" * pad + "\n")
            written = target_bytes
    size = path.stat().st_size
    if exact:
        assert size == target_bytes, (size, target_bytes)
    return {"path": str(path), "bytes": size}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--mb", type=float)
    ap.add_argument("--exact-bytes", type=int)
    a = ap.parse_args()
    if a.exact_bytes:
        print(generate(a.out, a.exact_bytes, True))
    elif a.mb:
        print(generate(a.out, int(a.mb * 1024 * 1024)))
    else:
        sys.exit("give --mb or --exact-bytes")
