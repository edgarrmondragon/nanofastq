# Copyright 2026 Edgar Ramírez-Mondragón

"""Download the benchmark FASTQ files from ENA and write plain copies next to them.

Usage: python fetch_data.py [DATA_DIR]   (default: ./data)
"""

import gzip
import shutil
import sys
import urllib.request
from pathlib import Path

BASE = "https://ftp.sra.ebi.ac.uk/vol1/fastq"
FILES = [
    # 643k reads of ~120 bp (119 MB gzipped)
    f"{BASE}/SRR177/003/SRR1770413/SRR1770413_1.fastq.gz",
    # 7.2M reads of 36 bp (101 MB gzipped)
    f"{BASE}/SRR390/SRR390728/SRR390728_1.fastq.gz",
]

data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
data.mkdir(parents=True, exist_ok=True)
for url in FILES:
    gz = data / url.rsplit("/", 1)[1]
    if not gz.exists():
        print("downloading", url)
        urllib.request.urlretrieve(url, gz)  # noqa: S310
    plain = gz.with_suffix("")
    if not plain.exists():
        print("decompressing", gz.name)
        with gzip.open(gz, "rb") as src, plain.open("wb") as dst:
            shutil.copyfileobj(src, dst)
