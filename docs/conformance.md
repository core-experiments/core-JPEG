# JPEG 2000 conformance

## Contract

The decoder contract is ISO/IEC 15444-1:2024 (JPEG 2000 Part 1). A successful
decode must preserve the codestream's component count, component dimensions,
precision, signedness, and level-shift-corrected integer samples. Features
specified only by other parts, such as HTJ2K from Part 15, are out of scope
unless the project explicitly adopts them.

Conformance to that contract requires the applicable ISO/IEC 15444-4
conformance procedures and their normative electronic attachment. Passing a
differential comparison against OpenJPEG is useful evidence, but is not by
itself a normative conformance result.

## Native and display output

`DecodedJpxComponent.samples` is the native component plane. It retains signed
values and precision above eight bits. The historical component `data` and
image `interleaved` fields are display-oriented byte output and may be shifted,
scaled, transformed, or clamped. The differential harness compares native
samples and requires an exact component count; it must not compare display
bytes to PGX samples.

## Corpora and provenance

`openjpeg-data` is pinned as a submodule. It contains OpenJPEG regression data
and an older Part 4-derived conformance set. Its provenance is documented by
the submodule itself, and it must not be described as verification against the
current normative ISO attachment.

The official ISO/IEC 15444-4:2024 electronic attachment has been acquired and
verified locally for the checked-in baseline. Its archive SHA-256 is
`ac04b52e1fe38404912036c14f215099ea9a785f38644fbe76ae8f3d1523c86d` and its
source is the ISO standards attachment URL recorded in the baseline. The
attachment is not redistributed by this repository. Obtain it from ISO, retain
its supplied license and provenance records, and mount or copy its extracted
`files` directory at `external/iso15444-4/` (or pass `--data-root`).

Applicability decisions live in `tools/openjpeg_applicability.toml`. Rules must
state why a fixture is `known_unsupported` or `out_of_scope`; oracle execution
problems are reported separately as `oracle_failure`, and malformed/missing
inputs as `invalid_fixture`.

## Local commands

```sh
git submodule update --init --recursive
tools/build_openjpeg.sh
uv run python tools/openjpeg_conformance.py \
  --corpus openjpeg-input \
  --json-output .build/openjpeg-input-report.json
```

For an authorized normative attachment:

```sh
uv run python tools/openjpeg_conformance.py \
  --corpus iso15444-4 \
  --profile 1 \
  --data-root /path/to/electronic-attachment/files \
  --json-output .build/iso15444-4-report.json
```

The normative runner reads reference PGX components directly and does not use
or require `opj_decompress`. It implements Tables C.6 and C.7 peak-error and
MSE limits against `decoded.native_components`. A formal Profile 1 run includes
all Profile 0 cases. Case `p0_08` requires resolution reduction 1; until the
decoder has a reduced-resolution API, it is reported deterministically as a
`bug` rather than being compared after non-normative downsampling.

The `openjpeg-input` corpus remains a differential suite and does require
`opj_decompress`.

Run focused harness tests and lint with:

```sh
uv run pytest tests/test_openjpeg_conformance.py
uv run ruff check tools/openjpeg_conformance.py tests/test_openjpeg_conformance.py
```
