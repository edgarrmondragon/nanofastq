# Copyright 2026 Edgar Ramírez-Mondragón

"""Test the SBOM shipped in the wheel's metadata (PEP 770)."""

import json
from importlib.metadata import distribution


def test_sbom_lists_compiled_in_components() -> None:
    files = [f for f in distribution("nanofastq").files or [] if "sboms" in f.parts]
    assert len(files) == 1
    sbom = json.loads(files[0].read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    names = {c["name"] for c in sbom["components"]}
    assert {"nanoarrow", "nanobind"} <= names
    assert all(c["version"] for c in sbom["components"])
