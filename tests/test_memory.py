# Copyright 2026 Edgar Ramírez-Mondragón

"""Check that reading a file batch by batch keeps memory flat (needs memray, so not on Windows)."""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import pyarrow as pa
import pytest

from nanofastq import read_fastq

if TYPE_CHECKING:
    from pathlib import Path

memray = pytest.importorskip("memray")

RECORD = b"@read%d sample=a\n" + b"ACGT" * 25 + b"\n+\n" + b"I" * 100 + b"\n"
BATCH_SIZE = 1024
SMALL = 20_000
LARGE = 10 * SMALL


def write_fastq(path: Path, n: int, *, gz: bool) -> Path:
    data = b"".join(RECORD % i for i in range(n))
    path.write_bytes(gzip.compress(data, compresslevel=1) if gz else data)
    return path


def peak_memory(path: Path, n: int, tmp_path: Path) -> int:
    """Stream the file, dropping each batch, and return the peak bytes allocated (native included)."""
    capture = tmp_path / f"{path.name}.memray"
    rows = 0
    with memray.Tracker(capture):
        for batch in pa.RecordBatchReader.from_stream(
            read_fastq(path, batch_size=BATCH_SIZE)
        ):
            rows += batch.num_rows
    assert rows == n
    return int(memray.FileReader(capture).metadata.peak_memory)


@pytest.mark.parametrize("gz", [False, True], ids=["plain", "gz"])
def test_peak_memory_does_not_grow_with_file_size(tmp_path: Path, *, gz: bool) -> None:
    suffix = ".fastq.gz" if gz else ".fastq"
    small = write_fastq(tmp_path / f"{SMALL}{suffix}", SMALL, gz=gz)
    large = write_fastq(tmp_path / f"{LARGE}{suffix}", LARGE, gz=gz)

    small_peak = peak_memory(small, SMALL, tmp_path)
    large_peak = peak_memory(large, LARGE, tmp_path)

    # 10x the records (and hundreds more batches) must not cost more memory: that would mean the
    # reader buffers the file, or leaks something per batch.
    assert large_peak < 1.25 * small_peak, (small_peak, large_peak)
    # And the peak is a small fraction of the uncompressed data.
    assert large_peak < LARGE * len(RECORD) / 10, large_peak


def consume(path: Path, how: str) -> None:
    reader = pa.RecordBatchReader.from_stream(read_fastq(path, batch_size=BATCH_SIZE))
    try:
        for _ in reader:
            if how == "partial":
                break
    except pa.ArrowInvalid:
        assert how == "error"
    else:
        assert how != "error"
    reader.close()


@pytest.mark.parametrize("how", ["complete", "partial", "error"])
@pytest.mark.parametrize("gz", [False, True], ids=["plain", "gz"])
def test_released_stream_frees_everything(
    tmp_path: Path, how: str, *, gz: bool
) -> None:
    path = write_fastq(tmp_path / ("x.fastq.gz" if gz else "x.fastq"), SMALL, gz=gz)
    if how == "error":
        # A record whose sequence and quality lengths differ, after enough good ones to have
        # emitted many batches.
        bad = b"@bad\nACGT\n+\nII\n"
        path.write_bytes(path.read_bytes() + (gzip.compress(bad) if gz else bad))
    consume(path, how)  # warm up one-time caches in pyarrow outside the capture

    capture = tmp_path / "leaks.memray"
    with memray.Tracker(capture, native_traces=True):
        consume(path, how)  # every object it makes is gone once it returns

    leaked = list(
        memray.FileReader(capture).get_leaked_allocation_records(merge_threads=True)
    )
    assert not leaked, [
        (r.size, [frame[0] for frame in r.native_stack_trace(max_stacks=8)])
        for r in leaked
    ]
