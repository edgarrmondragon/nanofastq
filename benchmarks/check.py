# Copyright 2026 Edgar Ramírez-Mondragón

"""Compare with biobear."""

import sys

import biobear as bb
import pyarrow as pa

import nanofastq

path = sys.argv[1]
mine = pa.table(nanofastq.read_fastq(path))
theirs = bb.connect().read_fastq_file(path).to_arrow()
print("rows:", mine.num_rows, theirs.num_rows)
print("nanofastq schema:", mine.schema.to_string().replace("\n", "; "))
print("biobear   schema:", theirs.schema.to_string().replace("\n", "; "))
for col in mine.column_names:
    a = mine[col].combine_chunks().cast(pa.large_string())
    b = theirs[col].combine_chunks().cast(pa.large_string())
    print(f"  {col}: equal={a.equals(b)}")
    if not a.equals(b):
        for i in range(len(a)):
            if a[i].as_py() != b[i].as_py():
                print(
                    "   first diff at",
                    i,
                    repr(a[i].as_py())[:80],
                    "|",
                    repr(b[i].as_py())[:80],
                )
                break
