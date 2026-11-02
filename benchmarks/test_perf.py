"""CodSpeed benchmarks: small, deterministic inputs (the simulator is slow, and counts must
be stable between runs). Run with `pytest benchmarks/test_perf.py --codspeed`.
"""

import gzip
import random

import pyarrow as pa
import pytest

from nanofastq import read_fastq

QUALS = "".join(chr(33 + q) for q in range(41))


def make_fastq(n_reads: int, read_len: int, *, utf8_headers: bool = False) -> bytes:
    rng = random.Random(0)  # fixed seed: identical bytes on every run
    out = []
    for i in range(n_reads):
        seq = "".join(rng.choices("ACGT", k=read_len))
        qual = "".join(rng.choices(QUALS, k=read_len))
        name = f"réad{i}" if utf8_headers else f"read{i}"
        out.append(f"@{name} lane=1 sample=a\n{seq}\n+\n{qual}\n")
    return "".join(out).encode()


# name -> (reads, read length, non-ASCII headers, gzip)
CASES = {
    "short-plain": (20_000, 36, False, False),
    "short-gz": (20_000, 36, False, True),
    "long-plain": (200, 10_000, False, False),
    "long-gz": (200, 10_000, False, True),
    "short-utf8-headers": (20_000, 36, True, False),  # exercises the strict UTF-8 fallback
}


@pytest.fixture(scope="session")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("fastq")
    paths = {}
    for name, (n, length, utf8, gz) in CASES.items():
        data = make_fastq(n, length, utf8_headers=utf8)
        p = d / (f"{name}.fastq.gz" if gz else f"{name}.fastq")
        p.write_bytes(gzip.compress(data, 1, mtime=0) if gz else data)
        paths[name] = p
    return paths


@pytest.mark.parametrize("case", CASES)
def test_read_table(benchmark, files, case):
    path = files[case]
    n_reads = CASES[case][0]
    assert benchmark(lambda: pa.table(read_fastq(path)).num_rows) == n_reads


def test_stream_small_batches(benchmark, files):
    """Per-batch overhead: many small batches instead of a few large ones."""
    path = files["short-plain"]

    def run():
        reader = pa.RecordBatchReader.from_stream(read_fastq(path, batch_size=512))
        return sum(len(b) for b in reader)

    assert benchmark(run) == CASES["short-plain"][0]
