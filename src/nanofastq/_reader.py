# Copyright 2026 Edgar Ramírez-Mondragón

"""Tiny FASTQ reader exposing the Arrow PyCapsule stream interface."""

from __future__ import annotations

import os

from nanofastq import _core

__all__ = ["FastqReader", "read_fastq"]


class FastqReader:
    """A lazily-read FASTQ file.

    Implements ``__arrow_c_stream__`` so it can be passed directly to
    pyarrow, polars, duckdb, pandas, etc. Each call opens the file afresh, so
    consumers that probe the schema first (e.g. duckdb) work, and the object
    can be scanned more than once.
    """

    def __init__(self, path: str | os.PathLike[str], batch_size: int = 65536) -> None:
        self._path = os.fspath(path)
        self._batch_size = batch_size
        _core.open_stream(self._path, batch_size)  # fail early if unreadable

    def __arrow_c_stream__(self, requested_schema: object | None = None) -> object:  # ruff: ignore[bad-dunder-method-name]
        return _core.open_stream(self._path, self._batch_size)


def read_fastq(path: str | os.PathLike[str], batch_size: int = 65536) -> FastqReader:
    """Read a FASTQ file into an nanoarrow array stream.

    Returns:
        An Arrow array stream.
    """
    return FastqReader(path, batch_size)
