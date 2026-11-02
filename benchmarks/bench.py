"""Compare nanofastq with biobear, each run in a fresh process.

Usage: python bench.py DATA_DIR [RUNS]   (needs biobear: see README)
"""

import json
import statistics as st
import subprocess
import sys
from pathlib import Path

data = Path(sys.argv[1])
runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5
run_one = Path(__file__).with_name("run_one.py")

IMPLS = ["nanofastq", "biobear", "nanofastq-stream", "biobear-stream"]
CASES = [
    ("SRR1770413_1.fastq.gz", ["inflate-only", *IMPLS]),
    ("SRR1770413_1.fastq", ["read-only", *IMPLS]),
    ("SRR390728_1.fastq.gz", ["inflate-only", *IMPLS]),
    ("SRR390728_1.fastq", ["read-only", *IMPLS]),
]

for name, impls in CASES:
    for impl in impls:
        res = []
        for _ in range(runs + 1):  # first run is a warm-up (page cache)
            p = subprocess.run(
                [sys.executable, "-I", str(run_one), impl, str(data / name)],
                capture_output=True,
                text=True,
                check=True,
            )
            res.append(json.loads(p.stdout.splitlines()[-1]))
        res = res[1:]
        print(
            f"{name:22} {impl:16} "
            f"med={st.median(r['wall'] for r in res):6.2f}s "
            f"min={min(r['wall'] for r in res):6.2f}s "
            f"cpu={st.median(r['cpu'] for r in res):6.2f}s "
            f"rss={max(r['rss_mb'] for r in res):6.0f}MB",
            flush=True,
        )
