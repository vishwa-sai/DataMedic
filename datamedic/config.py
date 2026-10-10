"""Central configuration. Every limit lives here so it stays consistent."""
from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# ---- Upload limit: 500 MB (524,288,000 bytes) per source file -------------
MAX_FILE_BYTES = 500 * 1024 * 1024
# Multipart framing (boundaries, headers) is not part of the file itself.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MAX_CONTENT_LENGTH = MAX_FILE_BYTES + MULTIPART_OVERHEAD_BYTES

ALLOWED_EXTENSIONS = (".csv", ".xlsx", ".xls")

# ---- Workbook safety ------------------------------------------------------
MAX_XLSX_UNCOMPRESSED_BYTES = 2 * 1024 ** 3     # zip-bomb guard
MAX_XLSX_COMPRESSION_RATIO = 200
MAX_COLUMNS = 2000
XLSX_MAX_ROWS = 1_048_575                       # Excel limit minus header
EXCEL_CONVERT_TIMEOUT_S = 900

# ---- Processing -----------------------------------------------------------
TARGET_CELLS_PER_CHUNK = 2_000_000
MIN_CHUNK_ROWS = 2_000
MAX_CHUNK_ROWS = 200_000
MAX_WORKERS = 2                                  # bounded concurrency
MAX_CONCURRENT_PREVIEWS = 2
JOB_TIMEOUT_S = 3600
MAX_MEDIAN_VALUES = 30_000_000
MAX_MODE_DISTINCT = 2_000_000

# ---- Preview --------------------------------------------------------------
PREVIEW_ROWS = 40
PREVIEW_COLS = 30
# Files up to this size get an exact full-dataset dry run for previews.
DRY_RUN_MAX_BYTES = 64 * 1024 * 1024
SAMPLE_PREVIEW_ROWS = 2000

# ---- Housekeeping ---------------------------------------------------------
JOB_TTL_HOURS = 24
CLEANUP_INTERVAL_S = 1800


def data_dir() -> Path:
    return Path(os.environ.get("DATAMEDIC_DATA_DIR", ROOT_DIR / "instance")).resolve()
