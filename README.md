# nanofastq

[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://codspeed.io/edgarrmondragon/nanofastq)

Tiny [FASTQ] reader (C + nanoarrow) exposing `__arrow_c_stream__`.

```python
import pyarrow as pa
from nanofastq import read_fastq

table = pa.table(read_fastq("reads.fastq.gz"))
```

## Development

Run tests (requires [Tox]):

```
tox p
```

Performance is tracked with [CodSpeed]. Running the benchmarks locally only checks that they
work; the reported numbers come from CI:

```
uv run pytest benchmarks/test_perf.py --codspeed
```

## Releasing

The version is derived from git tags with [setuptools-scm]; there is no version to edit in
`pyproject.toml`. To release, publish a GitHub release that creates a new `vX.Y.Z` tag. That
builds the wheels and uploads them to PyPI (see `.github/workflows/wheels.yml`). Untagged
commits get development versions such as `0.1.dev12+gabc1234`.

[codspeed]: https://codspeed.io
[fastq]: https://en.wikipedia.org/wiki/FASTQ_format
[setuptools-scm]: https://setuptools-scm.readthedocs.io
[tox]: https://tox.wiki
