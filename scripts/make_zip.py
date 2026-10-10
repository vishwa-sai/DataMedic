"""Create dist/DataMedic.zip (source, docs, samples, tests - no venv, no job data).

    python scripts/make_zip.py
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "instance", "__pycache__", ".pytest_cache", "dist", ".git", "node_modules", "_previous_prototype"}
SKIP_FILES = {"datamedic.log"}


def main():
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / "DataMedic.zip"
    n = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_dir() or SKIP_DIRS & set(rel.parts) or p.name in SKIP_FILES or p.suffix == ".pyc":
                continue
            z.write(p, Path("DataMedic") / rel)
            n += 1
    print(f"{target}  ({n} files, {target.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
