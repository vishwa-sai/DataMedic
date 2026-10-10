"""Cross-chunk duplicate detection using 64-bit row hashes.

Memory cost is 8 bytes per DISTINCT row (e.g. 80 MB for 10 million rows) instead
of holding the data. Hash collisions are theoretically possible (probability
~ n^2 / 2^65: about 3 in a million for 10 million rows) and are documented in
the README as a known limitation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def row_hashes(df: pd.DataFrame) -> np.ndarray:
    return pd.util.hash_pandas_object(df, index=False).to_numpy(dtype="uint64")


class DupTracker:
    def __init__(self):
        self.seen = np.empty(0, dtype="uint64")   # sorted, unique

    def add(self, hashes: np.ndarray) -> np.ndarray:
        """Return a boolean mask: True where the row duplicates an EARLIER row
        (in this chunk or any previous chunk). The first occurrence is kept."""
        if len(hashes) == 0:
            return np.zeros(0, bool)
        within = pd.Series(hashes).duplicated().to_numpy()
        if len(self.seen):
            pos = np.searchsorted(self.seen, hashes)
            pos[pos >= len(self.seen)] = len(self.seen) - 1
            cross = self.seen[pos] == hashes
        else:
            cross = np.zeros(len(hashes), bool)
        dup = within | cross
        new = np.unique(hashes[~dup])
        if len(new):
            self.seen = np.insert(self.seen, np.searchsorted(self.seen, new), new)
        return dup
