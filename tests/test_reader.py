import gzip
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
