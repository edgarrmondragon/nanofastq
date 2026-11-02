import gzip
import zlib
from pathlib import Path

import pyarrow as pa
import pytest

from nanofastq import read_fastq

DATA = b"""\
@r1 sample=a
ACGT
+
IIII
@r2
GG
+r2
#!
"""


def write_lines(p: Path, lines: list[str]):
    p.write_text("\n".join(lines) + "\n")


def write_bytes(p: Path, lines: list[bytes]):
    p.write_bytes(b"\n".join(lines) + b"\n")


@pytest.fixture(params=["plain", "gz"])
def fastq(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    if request.param == "gz":
        p = tmp_path / "x.fastq.gz"
        p.write_bytes(gzip.compress(DATA))
    else:
        p = tmp_path / "x.fastq"
        p.write_bytes(DATA)
    return p


def test_roundtrip(fastq: Path):
    t = pa.table(read_fastq(fastq))
    assert t.to_pylist() == [
        {
            "name": "r1",
            "description": "sample=a",
            "sequence": "ACGT",
            "quality_scores": "IIII",
        },
        {"name": "r2", "description": None, "sequence": "GG", "quality_scores": "#!"},
    ]


def test_small_batches(fastq):
    reader = pa.RecordBatchReader.from_stream(read_fastq(fastq, batch_size=1))
    assert [len(b) for b in reader] == [1, 1]


def test_rescan(fastq):
    r = read_fastq(fastq)
    assert pa.table(r).equals(pa.table(r))


def test_polars_and_duckdb(fastq):
    duckdb = pytest.importorskip("duckdb")
    pl = pytest.importorskip("polars")

    assert pl.DataFrame(read_fastq(fastq)).height == 2
    r = read_fastq(fastq)  # noqa: F841 (duckdb replacement scan)
    assert duckdb.execute("select count(*) from r").fetchall() == [(2,)]


def test_malformed(tmp_path: Path):
    p = tmp_path / "bad.fastq"
    write_lines(p, ["@r1", "ACGT", "+", "II"])  # quality too short
    with pytest.raises(pa.ArrowInvalid, match="lengths differ"):
        pa.table(read_fastq(p))


def test_missing_file(tmp_path: Path):
    with pytest.raises(ValueError, match="cannot open"):
        read_fastq(tmp_path / "nope.fastq")


def test_utf8_header_ok(tmp_path: Path):
    p = tmp_path / "u.fastq"
    p.write_bytes("@r\u00e9ad \u2603 \U0001f9ec\nAC\n+\nII\n".encode())
    write_bytes(
        p,
        [
            "@r\u00e9ad \u2603 \U0001f9ec".encode(),
            b"AC",
            b"+",
            b"II",
        ],
    )
    t = pa.table(read_fastq(p))
    assert t["name"].to_pylist() == ["r\u00e9ad"]
    assert t["description"].to_pylist() == ["\u2603 \U0001f9ec"]


@pytest.mark.parametrize(
    "bad",
    [
        b"\xff",  # never valid
        b"\xc0\x80",  # overlong NUL
        b"\xe0\x80\x80",  # overlong
        b"\xed\xa0\x80",  # surrogate
        b"\xf4\x90\x80\x80",  # > U+10FFFF
        b"\xe2\x98",  # truncated
        b"\xc3",  # truncated at end of field
    ],
)
@pytest.mark.parametrize("where", ["header", "sequence", "quality"])
def test_invalid_utf8(tmp_path: Path, bad: bytes, where: str):
    hdr, seq, qual = b"r1", b"A" * len(bad), b"I" * len(bad)
    if where == "header":
        hdr = b"r1 " + bad
    elif where == "sequence":
        seq = bad
    else:
        qual = bad
    p = tmp_path / "bad.fastq"
    write_bytes(
        p,
        [
            b"@" + hdr,
            seq,
            b"+",
            qual,
        ],
    )
    with pytest.raises(pa.ArrowInvalid, match="not valid UTF-8"):
        pa.table(read_fastq(p))


def test_good_rows_before_error_are_kept(tmp_path: Path):
    p = tmp_path / "partial.fastq"
    write_lines(
        p,
        [
            "@a",
            "AC",
            "+",
            "II",
            "@b",
            "GG",
            "+",
            "##",
            "@c",
            "ACGT",
            "+",
            "II",
            "@d",
            "T",
            "+",
            "I",
        ],
    )
    reader = pa.RecordBatchReader.from_stream(read_fastq(p, batch_size=100))
    first = reader.read_next_batch()
    assert first.column("name").to_pylist() == ["a", "b"]
    with pytest.raises(pa.ArrowInvalid, match="record 3: sequence and quality"):
        reader.read_next_batch()


def test_error_in_first_row_of_batch(tmp_path: Path):
    p = tmp_path / "first.fastq"
    write_lines(
        p,
        [
            "@a",
            "AC",
            "+",
            "II",
            "@b",
            "GG",
            "+",
            "##",
            "@c",
            "ACGT",
            "+",
            "II",
        ],
    )
    reader = pa.RecordBatchReader.from_stream(read_fastq(p, batch_size=2))
    assert len(reader.read_next_batch()) == 2
    with pytest.raises(pa.ArrowInvalid, match="record 3"):
        reader.read_next_batch()


def test_record_spanning_buffer_refills(tmp_path: Path):
    # Lines far larger than the initial buffer force compaction/realloc mid-record.
    n = 5_000_000
    p = tmp_path / "big.fastq"
    write_lines(
        p,
        [
            "@big desc",
            "A" * n,
            "+",
            "I" * n,
            "@s",
            "AC",
            "+",
            "II",
        ],
    )
    t = pa.table(read_fastq(p))
    assert t["name"].to_pylist() == ["big", "s"]

    seq0 = t["sequence"][0]
    assert isinstance(seq0, pa.StringScalar)
    assert len(seq0.as_py()) == n

    qual1 = t["quality_scores"][1]
    assert isinstance(qual1, pa.StringScalar)
    assert qual1.as_py() == "II"


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        pytest.param(
            ["garbage"],
            "does not start with '@'",
            id="no-at-start",
        ),
        pytest.param(
            ["@a"],
            "truncated after header",
            id="truncated-after-header",
        ),
        pytest.param(
            ["@a", "AC"],
            "missing '\\+'",
            id="missing-plus-after-sequence",
        ),
        pytest.param(
            ["@a", "AC", "x", "II"],
            "missing '\\+'",
            id="missing-plus-after-quality",
        ),
        pytest.param(
            ["@a", "AC", "+"],
            "truncated before quality",
            id="truncated-before-quality",
        ),
    ],
)
def test_structural_errors(tmp_path: Path, data: list[str], msg: str):
    p = tmp_path / "s.fastq"
    write_lines(p, data)
    with pytest.raises(pa.ArrowInvalid, match=msg):
        pa.table(read_fastq(p))


def record(i: int) -> bytes:
    return b"@r%d\nACGT\n+\nIIII\n" % i


def records(n: int) -> bytes:
    return b"".join(record(i) for i in range(n))


def gzip_flushed_per_record(n: int) -> tuple[bytes, int]:
    """Return a gzip stream of n records, flushed after each one, and the length of the prefix
    that ends exactly on the record boundary after n // 2 records (before any trailer)."""
    c = zlib.compressobj(wbits=31)
    out = b""
    cut = 0
    for i in range(n):
        out += c.compress(record(i)) + c.flush(zlib.Z_SYNC_FLUSH)
        if i == n // 2 - 1:
            cut = len(out)
    return out + c.flush(), cut


def test_gzip_truncated_at_record_boundary(tmp_path: Path):
    # A download that stopped exactly between records used to return the first half of the
    # records without any error.
    full, cut = gzip_flushed_per_record(20)
    p = tmp_path / "cut.fastq.gz"
    p.write_bytes(full[:cut])
    with pytest.raises(OSError, match="unexpected end of file"):
        pa.table(read_fastq(p))


def test_gzip_truncated_keeps_good_rows_first(tmp_path: Path):
    full, cut = gzip_flushed_per_record(20)
    p = tmp_path / "cut.fastq.gz"
    p.write_bytes(full[:cut])
    reader = pa.RecordBatchReader.from_stream(read_fastq(p, batch_size=100))
    assert len(reader.read_next_batch()) == 10
    with pytest.raises(OSError, match="unexpected end of file"):
        reader.read_next_batch()


def test_gzip_missing_trailer(tmp_path: Path):
    # All the data is there but the CRC and length trailer are cut off.
    p = tmp_path / "notrailer.fastq.gz"
    p.write_bytes(gzip.compress(records(100))[:-8])
    with pytest.raises(OSError, match="unexpected end of file"):
        pa.table(read_fastq(p))


@pytest.mark.parametrize("keep", [0.1, 0.5, 0.9])
def test_gzip_truncated_mid_stream(tmp_path: Path, keep: float):
    gz = gzip.compress(records(1000))
    p = tmp_path / "trunc.fastq.gz"
    p.write_bytes(gz[: int(len(gz) * keep)])
    with pytest.raises(OSError, match="unexpected end of file"):
        pa.table(read_fastq(p))


@pytest.mark.parametrize("what", ["flipped-byte", "bad-crc"])
def test_gzip_corrupt(tmp_path: Path, what: str):
    gz = bytearray(gzip.compress(records(1000)))
    gz[len(gz) // 2 if what == "flipped-byte" else -8] ^= 0xFF
    p = tmp_path / "corrupt.fastq.gz"
    p.write_bytes(bytes(gz))
    with pytest.raises(OSError, match="read error"):
        pa.table(read_fastq(p))


@pytest.mark.parametrize(
    ("name", "content", "rows"),
    [
        pytest.param("empty.fastq", b"", 0, id="empty"),
        pytest.param("blank.fastq", b"\n\n\n", 0, id="only-blank-lines"),
        pytest.param("crlf.fastq", records(5).replace(b"\n", b"\r\n"), 5, id="crlf"),
        pytest.param("nonl.fastq", records(5)[:-1], 5, id="no-trailing-newline"),
        pytest.param("plain.fastq.gz", records(5), 5, id="plain-text-named-gz"),
        pytest.param(
            "multi.fastq.gz",
            gzip.compress(records(5)) + gzip.compress(records(5)),
            10,
            id="concatenated-gzip-members",
        ),
    ],
)
def test_accepted_inputs(tmp_path: Path, name: str, content: bytes, rows: int):
    p = tmp_path / name
    p.write_bytes(content)
    assert pa.table(read_fastq(p)).num_rows == rows


def test_directory_is_an_error(tmp_path: Path):
    with pytest.raises((OSError, ValueError)):
        pa.table(read_fastq(tmp_path))


@pytest.mark.parametrize("batch_size", [1, 65536])
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param(
            b"@ desc\nAC\n+\nII\n",
            [("", "desc", "AC", "II")],
            id="empty-name-with-description",
        ),
        pytest.param(
            b"@\nAC\n+\nII\n", [("", None, "AC", "II")], id="empty-name-no-description"
        ),
        pytest.param(b"@r\n\n+\n\n", [("r", None, "", "")], id="empty-sequence"),
        pytest.param(
            b"@r\n\n+\n\n@s\nAC\n+\nII\n",
            [("r", None, "", ""), ("s", None, "AC", "II")],
            id="empty-sequence-then-normal",
        ),
        pytest.param(
            b"@s\nAC\n+\nII\n@r\n\n+\n\n",
            [("s", None, "AC", "II"), ("r", None, "", "")],
            id="normal-then-empty-sequence",
        ),
        pytest.param(b"@\n\n+\n\n", [("", None, "", "")], id="all-empty"),
    ],
)
def test_empty_values(
    tmp_path: Path, data: bytes, expected: list[tuple], batch_size: int
):
    # With batch_size=1 every record is the first value of its batch, when nothing has been
    # allocated yet. (The undefined behaviour this once hit there has no visible symptom, so it is
    # only caught by a sanitizer; this pins down the output.)
    p = tmp_path / "empty.fastq"
    p.write_bytes(data)
    rows = pa.table(read_fastq(p, batch_size=batch_size)).to_pylist()
    got = [
        (r["name"], r["description"], r["sequence"], r["quality_scores"]) for r in rows
    ]
    assert got == expected
