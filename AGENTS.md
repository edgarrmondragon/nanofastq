# AGENTS.md

nanofastq is a FASTQ reader written in C (nanoarrow + zlib), wrapped with nanobind, that exposes the Arrow PyCapsule stream interface (`__arrow_c_stream__`).

## Layout

- `src/fastq.c`, `src/fastq.h`: the parser, which builds Arrow record batches.
- `src/module.cc`: the nanobind wrapper (`open_stream`).
- `src/nanofastq/`: the Python API. `_core.pyi` is a hand-written stub for the compiled module.
- `tests/`: pytest. `benchmarks/`: CodSpeed benchmarks (`test_perf.py`) and a biobear comparison (see its README).

## Commands

- Test: `uv run pytest`. Every supported Python plus the type check: `tox p`.
- Type check: `tox -e type` (mypy and ty; nothing to compile).
- Lint and format: `pre-commit run -a`.
- Benchmarks: `uv run pytest benchmarks/test_perf.py --codspeed` (only checks they run; the numbers come from CI).

## Gotchas

- Keep `_core.pyi` in sync with `module.cc`; nothing checks it.
- The version comes from git tags (setuptools-scm). Don't set it in `pyproject.toml`; any job that builds the package needs `fetch-depth: 0`.
- The parser validates a whole record (structure, lengths, UTF-8) before appending it, so a bad record never leaves a half-built row. Keep it that way.
- Output must stay identical to biobear's (`benchmarks/check.py`).
- Workflow actions are pinned by SHA with a `# vX.Y.Z` comment, and runner images are pinned too; Renovate updates both. zizmor (pedantic) runs in pre-commit.
- PR titles use Conventional Commits (`feat:`, `fix:`, `ci:`, `chore(deps):`, ...). `.github/semantic.yml` enforces this, and Release Drafter derives labels, sections and version bumps from them.
- Never name a branch `renovate`: it collides with the Renovate app's `renovate/*` branches.
- CodSpeed can flag a roughly 12% "regression" on untouched code when the run landed on different hardware. Re-run before chasing it.
