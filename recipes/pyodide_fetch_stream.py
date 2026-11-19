# Copyright 2026 Edgar Ramírez-Mondragón

"""Stream a FASTQ download into nanofastq in Pyodide, without holding the whole file.

nanofastq reads its input synchronously, but a browser's ``fetch()`` body can only be read
asynchronously. With JavaScript Promise Integration (JSPI), ``pyodide.ffi.run_sync`` lets
synchronous code wait for a promise, so a plain file object can pull the body chunk by chunk.
Without JSPI, ``open_url`` falls back to downloading the whole file first.

Usage, in a Pyodide notebook (JupyterLite, marimo, ...)::

    records = read_fastq(await open_url("https://example.org/reads.fastq.gz"))

Copy this file into your project; it is not part of the nanofastq package.
"""

from __future__ import annotations

import io
from typing import override

import js
from pyodide.ffi import can_run_sync, run_sync
from pyodide.http import pyfetch

__all__ = ["FetchStream", "open_url"]


class FetchStream(io.RawIOBase):
    """A non-seekable binary file over a streaming ``fetch()`` body.

    Needs JSPI (check ``pyodide.ffi.can_run_sync()``). Like any non-seekable
    file object, the stream ``read_fastq`` returns can be scanned once.
    """

    def __init__(self, url: str) -> None:
        """Start the request and wait for the response headers.

        Raises:
            OSError: The server did not answer with a 2xx status.
        """
        super().__init__()
        resp = run_sync(js.fetch(url))
        if not resp.ok:
            msg = f"HTTP {resp.status} for {url}"
            raise OSError(msg)
        self._reader = resp.body.getReader()
        self._buf = memoryview(b"")

    @override
    def readable(self) -> bool:
        """Return True: the body can be read."""
        return True

    @override
    def readinto(self, buffer: memoryview, /) -> int:
        """Fill ``buffer`` from the body, waiting for the next chunk when needed.

        Returns:
            The number of bytes written, 0 at the end of the body.
        """
        while not len(self._buf):
            chunk = run_sync(self._reader.read())
            if chunk.done:
                return 0
            self._buf = memoryview(chunk.value.to_bytes())
        n = min(len(buffer), len(self._buf))
        buffer[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


async def open_url(url: str) -> io.RawIOBase | io.BytesIO:
    """Open ``url`` for ``read_fastq``: streamed with JSPI, else downloaded whole.

    Returns:
        A binary file object.
    """
    if can_run_sync():
        return FetchStream(url)
    resp = await pyfetch(url)
    return io.BytesIO(await resp.bytes())
