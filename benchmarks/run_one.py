# Copyright 2026 Edgar Ramírez-Mondragón

"""Run one implementation once in this process; print a JSON line.

Imports happen before the timer starts and are reported separately.
"""

import json
import pathlib
import resource
import sys
import time
import zlib

impl, path = sys.argv[1], sys.argv[2]

t_imp = time.perf_counter()
if impl.startswith("nanofastq"):
    import pyarrow as pa

    import nanofastq
elif impl.startswith("biobear"):
    import biobear as bb
    import pyarrow as pa
t_imp = time.perf_counter() - t_imp


def run():
    if impl == "nanofastq":
        return pa.table(nanofastq.read_fastq(path)).num_rows
    if impl == "nanofastq-stream":
        r = pa.RecordBatchReader.from_stream(nanofastq.read_fastq(path))
        return sum(len(b) for b in r)
    if impl == "biobear":
        return bb.connect().read_fastq_file(path).to_arrow().num_rows
    if impl == "biobear-stream":
        r = bb.connect().read_fastq_file(path).to_arrow_record_batch_reader()
        return sum(len(b) for b in r)
    if impl == "inflate-only":  # floor: just decompress (system zlib), no parsing
        n = 0
        d = zlib.decompressobj(31)
        with pathlib.Path(path).open("rb") as f:
            while chunk := f.read(1 << 20):
                n += len(d.decompress(chunk))
        return n
    if impl == "read-only":  # floor for uncompressed: just read the bytes
        n = 0
        with pathlib.Path(path).open("rb") as f:
            while chunk := f.read(1 << 20):
                n += len(chunk)
        return n
    msg = f"unknown impl {impl}"
    raise SystemExit(msg)


ru0 = resource.getrusage(resource.RUSAGE_SELF)
t0 = time.perf_counter()
out = run()
wall = time.perf_counter() - t0
ru1 = resource.getrusage(resource.RUSAGE_SELF)
cpu = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
# ru_maxrss is in bytes on macOS and in KiB on Linux.
rss_div = 1e6 if sys.platform == "darwin" else 1e3
print(
    json.dumps(
        {
            "impl": impl,
            "wall": wall,
            "cpu": cpu,
            "rss_mb": ru1.ru_maxrss / rss_div,
            "imp": t_imp,
            "out": out,
        },
    ),
)
