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

[codspeed]: https://codspeed.io
[fastq]: https://en.wikipedia.org/wiki/FASTQ_format
[tox]: https://tox.wiki
