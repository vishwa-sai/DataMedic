"""User-safe exceptions. Messages here are shown to end users verbatim."""
from __future__ import annotations


class DataMedicError(Exception):
    def __init__(self, message: str, code: str = "error", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class JobCancelled(Exception):
    pass


class JobTimeout(DataMedicError):
    def __init__(self, seconds: int):
        super().__init__(
            f"Processing exceeded the {seconds // 60}-minute time limit and was stopped.",
            "timeout", 504)
