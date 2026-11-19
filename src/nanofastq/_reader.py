# Copyright 2026 Edgar Ramírez-Mondragón

"""Tiny FASTQ reader exposing the Arrow PyCapsule stream interface."""

from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

from nanofastq import _core

__all__ = ["BinaryReadable", "FastqReader", "read_fastq"]

type _ReadInto = Callable[[memoryview], int | None]


class BinaryReadable(Protocol):
    """A binary file object: anything with ``read(n) -> bytes``.

    ``readinto`` is used instead when the object has it, and ``seekable``,
    ``tell`` and ``seek`` when it is seekable.
    """

    def read(self, n: int, /) -> bytes: ...


class _ReadTracker:
    """Wraps a non-seekable file object to record whether anything was read from it."""

    def __init__(self, file: BinaryReadable) -> None:
        self.file = file
        self.used = False
        # Only offer readinto when the file has it, so the reader falls back to read() otherwise.
        readinto: _ReadInto | None = getattr(file, "readinto", None)
        if readinto is not None:
            self._file_readinto = readinto
            self.readinto = self._readinto

    def read(self, n: int, /) -> bytes:
        self.used = True
        return self.file.read(n)

    def _readinto(self, buffer: memoryview, /) -> int | None:
        self.used = True
        return self._file_readinto(buffer)


def _tell_if_seekable(file: BinaryReadable) -> int | None:
    """Return the file's position if it is seekable, else None."""
    try:
        if (seekable := getattr(file, "seekable", None)) is None or not seekable():
            return None
        return file.tell()  # type: ignore[attr-defined,no-any-return]  # ty: ignore[missing-attribute]
    except (AttributeError, OSError, ValueError):
        return None


class FastqReader:
    """A lazily-read FASTQ file.

    Implements ``__arrow_c_stream__`` so it can be passed directly to
    pyarrow, polars, duckdb, pandas, etc. Each call opens the file afresh, so
    consumers that probe the schema first (e.g. duckdb) work, and the object
    can be scanned more than once.

    A binary file object can be given instead of a path. A seekable one is read
    from its current position on every scan, and is not closed. A non-seekable
    one (a pipe, a socket, an HTTP response) can only be scanned once; opening a
    stream on it again raises ``ValueError``, unless nothing was read through the
    earlier stream.
    """

    def __init__(
        self,
        source: str | os.PathLike[str] | BinaryReadable,
        batch_size: int = 65536,
    ) -> None:
        self._batch_size = batch_size
        self._path: str | None = None
        self._file: BinaryReadable | None = None
        self._offset: int | None = None
        self._tracker: _ReadTracker | None = None
        if isinstance(source, (str, os.PathLike)):
            self._path = os.fspath(source)
            _core.open_stream(self._path, batch_size)  # fail early if unreadable
            return
        # Checked on `object`: the type says these cannot happen, but callers may not be typed.
        obj: object = source
        if isinstance(obj, io.TextIOBase):
            msg = "the file object must be opened in binary mode ('rb')"
            raise TypeError(msg)
        if not hasattr(obj, "read"):
            msg = f"expected a path or a binary file object, got {type(obj).__name__}"
            raise TypeError(msg)
        self._file = source
        self._offset = _tell_if_seekable(source)
        if self._offset is None:
            self._tracker = _ReadTracker(source)
        self._open()  # validates batch_size, as the path branch does

    def _open(self) -> object:
        if self._path is not None:
            return _core.open_stream(self._path, self._batch_size)
        if self._tracker is not None:
            if self._tracker.used:
                msg = "a non-seekable file object can only be read once"
                raise ValueError(msg)
            return _core.open_file_stream(self._tracker, self._batch_size)
        return _core.open_file_stream(self._file, self._batch_size, self._offset)

    def __arrow_c_stream__(self, requested_schema: object | None = None) -> object:
        return self._open()


def read_fastq(
    source: str | os.PathLike[str] | BinaryReadable,
    batch_size: int = 65536,
) -> FastqReader:
    """Read a FASTQ file, or a binary file object, into an Arrow array stream.

    The input may be gzip-compressed (detected from its content).

    Returns:
        An Arrow array stream.
    """
    return FastqReader(source, batch_size)
