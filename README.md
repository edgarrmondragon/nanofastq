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

## Examples

### Load into DuckDB

```python
import duckdb
from nanofastq import read_fastq

duckdb.register("fastq", read_fastq("example.fastq"))
duckdb.sql("select * from fastq").fetchall()
```

### Load into a Polars dataframe

Note: this also requires installing PyArrow.

```python
import polars as pl
from nanofastq import read_fastq

df = pl.DataFrame(read_fastq("example.fastq"))
```

### Reading file-like objects

Starting with nanofastq v0.0.5, `read_fastq` also accepts a binary file object (anything with `read(n) -> bytes`), such as an open file, `io.BytesIO`, an fsspec file or an HTTP response. Gzip is detected from the content. The file is not closed for you.

If the file object is [_seekable_][py-seekable], every scan starts from the position it had when `read_fastq` was called (as reported by [`.tell()`][py-tell]), whether or not the position moved in between. So the result can be scanned more than once, and two readers can read the same file object without affecting each other.

If it is not seekable (a pipe, `sys.stdin.buffer`, an HTTP response), it can only be scanned once: starting a second scan after the first has read data raises `ValueError`. Opening a stream without reading from it, as DuckDB does to get the schema, doesn't count.

### Pyodide and JupyterLite

nanofastq has wheels for Pyodide (Python 3.14). Install it with `await micropip.install("nanofastq")`, or `%pip install nanofastq` in JupyterLite.

A browser gives Python no sockets, so `urllib.request` and `requests` cannot download a file there. Fetch it with the browser instead and read it from memory:

```python
import io

from pyodide.http import pyfetch
from nanofastq import read_fastq

url = "https://raw.githubusercontent.com/biopython/biopython/master/Tests/Quality/example.fastq"
resp = await pyfetch(url)
records = read_fastq(io.BytesIO(await resp.bytes()))
```

This downloads the whole file before reading it, but only the downloaded bytes are kept: for a `.fastq.gz` that is the compressed size, and decompressing and parsing still stream one batch at a time. The server must allow cross-origin requests (`raw.githubusercontent.com` does).

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
[py-seekable]: https://docs.python.org/3/library/io.html#io.IOBase.seekable
[py-tell]: https://docs.python.org/3/library/io.html#io.IOBase.tell
[release drafter]: https://github.com/release-drafter/release-drafter
[setuptools-scm]: https://setuptools-scm.readthedocs.io
[tox]: https://tox.wiki
[ty]: https://docs.astral.sh/ty/
