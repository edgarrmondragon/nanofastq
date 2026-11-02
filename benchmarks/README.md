# Benchmarks

Compares `nanofastq` with [biobear](https://github.com/wheretrue/biobear) on real
Illumina FASTQ files from ENA. Each measurement runs in a fresh process (imports are
excluded from the timer); the first run of each case is a warm-up and the table shows the
median of the rest.

```console
$ uv venv --python 3.13 && uv pip install biobear pyarrow ..
$ python fetch_data.py data            # ~220 MB download, ~1.2 GB on disk
$ python check.py data/SRR1770413_1.fastq.gz   # both readers return identical columns
$ python bench.py data 5
```

`inflate-only` / `read-only` are floors: just decompressing (system zlib) or reading the
bytes, with no parsing.

## Results

Apple M3 Pro, Python 3.13, biobear 0.23.7, pyarrow 26.0, warm file cache. Median wall time
to build a full Arrow table:

| File | nanofastq | biobear | Floor |
|---|---|---|---|
| SRR1770413 `.gz` (643k × ~120 bp) | 0.50 s | 1.23 s | 0.36 s |
| SRR1770413 plain | 0.19 s | 0.56 s | 0.03 s |
| SRR390728 `.gz` (7.2M × 36 bp) | 1.02 s | 2.33 s | 0.33 s |
| SRR390728 plain | 0.81 s | 1.98 s | 0.04 s |

Streaming batch by batch gives the same ratios. Neither reader used more than one core.
One machine and two files: treat the ratios as indicative, not general.
