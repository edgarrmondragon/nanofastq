# nanofastq

[![License](https://img.shields.io/github/license/edgarrmondragon/nanofastq?color=blue)](https://github.com/edgarrmondragon/nanofastq/blob/main/LICENSE)
[![Python Versions](https://img.shields.io/pypi/pyversions/nanofastq.svg?logo=python&logoColor=FFE873&color=blue)](https://pypi.org/project/nanofastq/)
[![Package Version](https://img.shields.io/pypi/v/nanofastq.svg?logo=pypi&logoColor=FFE873&color=blue)](https://pypi.org/project/nanofastq/)
[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://codspeed.io/edgarrmondragon/nanofastq)

Tiny [FASTQ] reader (C + nanoarrow) exposing `__arrow_c_stream__`.

```python
import pyarrow as pa
from nanofastq import read_fastq

table = pa.table(read_fastq("reads.fastq.gz"))
```

## Development

Run the tests and the type check with [ty] (requires [Tox]):

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
`pyproject.toml`. [Release Drafter] keeps a draft GitHub release up to date from merged pull
requests (their labels pick the section and the next version). To release, review the draft and
publish it: that creates the `vX.Y.Z` tag, builds the wheels and uploads them to PyPI (see
`.github/workflows/wheels.yml`). Untagged commits get development versions such as
`0.1.dev12+gabc1234`.

## License

[MIT](LICENSE). The wheels also contain nanoarrow (Apache-2.0), nanobind (BSD-3-Clause) and, on
Windows, zlib; their notices are in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

[codspeed]: https://codspeed.io
[fastq]: https://en.wikipedia.org/wiki/FASTQ_format
[release drafter]: https://github.com/release-drafter/release-drafter
[setuptools-scm]: https://setuptools-scm.readthedocs.io
[tox]: https://tox.wiki
[ty]: https://docs.astral.sh/ty/
