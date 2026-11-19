# Copyright 2026 Edgar Ramírez-Mondragón

"""Test reading from binary file objects instead of paths."""

from __future__ import annotations

import gzip
import io
import threading
from typing import TYPE_CHECKING, Any, cast, override

import pyarrow as pa
import pytest

from nanofastq import read_fastq

if TYPE_CHECKING:
    from collections.abc import Buffer
    from pathlib import Path


def records(n: int) -> bytes:
    return b"".join(b"@r%d desc\nACGT\n+\nIIII\n" % i for i in range(n))


DATA = records(1000)
EXPECTED = pa.table(read_fastq(io.BytesIO(DATA)))


class ReadOnly:
    """Only has read(), and returns at most `chunk` bytes per call (short reads)."""

    def __init__(self, data: bytes, chunk: int = 7) -> None:
        self._f = io.BytesIO(data)
        self._chunk = chunk

    def read(self, n: int, /) -> bytes:
        return self._f.read(min(n, self._chunk))


class ShortReadinto(io.RawIOBase):
    """Non-seekable, with readinto() that fills at most `chunk` bytes per call."""

    def __init__(self, data: bytes, chunk: int = 5) -> None:
        self._f = io.BytesIO(data)
        self._chunk = chunk

    @override
    def readable(self) -> bool:
        return True

    @override
    def readinto(self, buffer: Buffer, /) -> int:
        return self._f.readinto(memoryview(buffer)[: self._chunk])


def test_expected_matches_path(tmp_path: Path) -> None:
    p = tmp_path / "x.fastq"
    p.write_bytes(DATA)
    assert EXPECTED.num_rows == 1000
    assert pa.table(read_fastq(p)).equals(EXPECTED)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(DATA, id="plain"),
        pytest.param(gzip.compress(DATA), id="gzip"),
        pytest.param(
            gzip.compress(DATA[:4000]) + gzip.compress(DATA[4000:]), id="gzip-members"
        ),
    ],
)
@pytest.mark.parametrize(
    "wrap",
    [
        pytest.param(io.BytesIO, id="BytesIO"),
        pytest.param(ReadOnly, id="read-only-short-reads"),
        pytest.param(ShortReadinto, id="readinto-short-reads"),
        pytest.param(
            lambda d: io.BufferedReader(ShortReadinto(d)), id="BufferedReader"
        ),
    ],
)
def test_reads_file_objects(data: bytes, wrap: type) -> None:
    assert pa.table(read_fastq(wrap(data), batch_size=100)).equals(EXPECTED)


def test_real_file_is_left_open(tmp_path: Path) -> None:
    p = tmp_path / "x.fastq.gz"
    p.write_bytes(gzip.compress(DATA))
    with p.open("rb") as f:
        assert pa.table(read_fastq(f)).equals(EXPECTED)
        assert not f.closed


def test_reads_from_current_position() -> None:
    f = io.BytesIO(b"junk before the records\n" + DATA)
    f.seek(24)
    assert pa.table(read_fastq(f)).equals(EXPECTED)


def test_seekable_rescan() -> None:
    f = io.BytesIO(gzip.compress(DATA))
    r = read_fastq(f)
    assert pa.table(r).equals(EXPECTED)
    f.read(10)  # moving the file in between does not matter
    assert pa.table(r).equals(EXPECTED)


def test_seekable_interleaved_streams() -> None:
    r = read_fastq(io.BytesIO(DATA), batch_size=10)
    a = pa.RecordBatchReader.from_stream(r)
    b = pa.RecordBatchReader.from_stream(r)
    batches_a, batches_b = [], []
    for x, y in zip(a, b, strict=True):
        batches_a.append(x)
        batches_b.append(y)
    assert pa.Table.from_batches(batches_a).equals(EXPECTED)
    assert pa.Table.from_batches(batches_b).equals(EXPECTED)


def test_non_seekable_reads_once() -> None:
    r = read_fastq(ReadOnly(DATA))
    assert pa.table(r).equals(EXPECTED)
    with pytest.raises(ValueError, match="only be read once"):
        pa.table(r)


def test_non_seekable_unread_stream_allows_another() -> None:
    # What a schema probe does (duckdb): open a stream, never read from it.
    r = read_fastq(ReadOnly(DATA))
    _ = pa.RecordBatchReader.from_stream(r).schema
    assert pa.table(r).equals(EXPECTED)


@pytest.mark.parametrize(
    "f",
    [
        pytest.param(
            io.StringIO("@r\nA\n+\nI\n"),
            id="StringIO",
        ),
        pytest.param(
            io.TextIOWrapper(io.BytesIO(DATA), encoding="utf-8"),
            id="TextIOWrapper",
        ),
    ],
)
def test_text_mode_rejected(f: Any) -> None:  # ruff: ignore[any-type] (deliberately the wrong type)
    with pytest.raises(TypeError, match="binary mode"):
        read_fastq(f)


def test_read_returning_str() -> None:
    class Text:
        data = "@r\nA\n+\nI\n"

        def read(self, n: int, /) -> str:
            return self.data[:n]

    text: Any = Text()  # deliberately not a BinaryReadable
    with pytest.raises(OSError, match="returned str, not bytes"):
        pa.table(read_fastq(text))


def test_not_a_file() -> None:
    with pytest.raises(
        TypeError, match="expected a path or a binary file object, got int"
    ):
        read_fastq(cast("Any", 42))


def test_bad_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        read_fastq(io.BytesIO(DATA), batch_size=0)


def test_read_exception_keeps_good_rows_first() -> None:
    class Failing:
        def __init__(self) -> None:
            self._f = io.BytesIO(records(200))

        def read(self, n: int, /) -> bytes:
            got = self._f.read(n)
            if not got:
                msg = "connection reset"
                raise ConnectionError(msg)
            return got

    reader = pa.RecordBatchReader.from_stream(read_fastq(Failing(), batch_size=100))
    assert len(reader.read_next_batch()) == 100
    assert len(reader.read_next_batch()) == 100
    with pytest.raises(OSError, match="ConnectionError: connection reset"):
        reader.read_next_batch()


def test_truncated_gzip() -> None:
    gz = gzip.compress(DATA)
    with pytest.raises(OSError, match="unexpected end of file"):
        pa.table(read_fastq(io.BytesIO(gz[: len(gz) // 2])))


def test_read_from_another_thread() -> None:
    result: list[pa.Table] = []
    r = read_fastq(ReadOnly(gzip.compress(DATA), chunk=1000), batch_size=10)
    t = threading.Thread(target=lambda: result.append(pa.table(r)))
    t.start()
    t.join()
    assert result[0].equals(EXPECTED)


def test_duckdb_and_polars() -> None:
    duckdb = pytest.importorskip("duckdb")
    pl = pytest.importorskip("polars")

    assert pl.DataFrame(read_fastq(io.BytesIO(DATA))).height == 1000
    con = duckdb.connect()
    con.register("fastq", read_fastq(io.BytesIO(gzip.compress(DATA)), batch_size=10))
    assert con.execute(
        "select count(*), count(distinct name) from fastq"
    ).fetchall() == [(1000, 1000)]
    # Non-seekable: duckdb probes the schema with one stream, then scans with another.
    con.register("once", read_fastq(ReadOnly(DATA, chunk=1000), batch_size=10))
    assert con.execute("select count(*) from once").fetchall() == [(1000,)]
