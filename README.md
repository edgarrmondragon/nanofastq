# nanofastq

Tiny FASTQ reader (C + nanoarrow) exposing `__arrow_c_stream__`.

```python
import pyarrow as pa
from nanofastq import read_fastq

table = pa.table(read_fastq("reads.fastq.gz"))
```

## Development

Run tests (requires [Tox](https://tox.wiki/)):

```
tox p
```
