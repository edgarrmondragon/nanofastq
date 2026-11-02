# Copyright 2026 Edgar Ramírez-Mondragón

"""Tiny FASTQ reader exposing the Arrow PyCapsule stream interface."""

from __future__ import annotations

from nanofastq._reader import FastqReader, read_fastq

__all__ = ["FastqReader", "read_fastq"]
