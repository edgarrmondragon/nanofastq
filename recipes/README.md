# Recipes

Small, self-contained helpers for using nanofastq in particular environments. They are not part of the package: copy the file you need into your project.

## Streaming a download in Pyodide

[`pyodide_fetch_stream.py`](pyodide_fetch_stream.py) streams a `fetch()` download into `read_fastq` in the browser, so the download never has to fit in memory whole:

```python
records = read_fastq(await open_url("https://example.org/reads.fastq.gz"))
```

nanofastq pulls its input synchronously, while a `fetch()` body can only be read asynchronously. With [JavaScript Promise Integration][jspi] (JSPI), [`pyodide.ffi.run_sync`][run-sync] lets the file object wait for each chunk. Without JSPI, `open_url` falls back to downloading the whole file into an `io.BytesIO`; check `pyodide.ffi.can_run_sync()` to see which one you get.

The stream is not seekable, so the result can be scanned once.

Measured in headless Chromium 159 with Pyodide 314.0.8, on a generated file served locally (wasm memory after the read; it starts at about 102 MB):

| Input | `io.BytesIO` | `FetchStream` |
| --- | --- | --- |
| 120 MB gzip, 1.5M records | 301 MB, 1.18 s | 178 MB, 1.12 s |
| 240 MB gzip, 3.0M records | 388 MB, 2.29 s | 178 MB, 2.19 s |

Streaming costs a fixed ~75 MB for nanofastq's buffers and one batch, whatever the file size. Not yet tried in Firefox or Safari, or over a real network.

[jspi]: https://v8.dev/blog/jspi
[run-sync]: https://pyodide.org/en/stable/usage/api/python-api/ffi.html#pyodide.ffi.run_sync
