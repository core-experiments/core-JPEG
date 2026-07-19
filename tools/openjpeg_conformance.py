"""JPEG 2000 differential and normative conformance harness."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import tempfile
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from shutil import which
from typing import Any

from core_jpeg import JpegError, decode_jpx_image

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "tools" / "openjpeg_applicability.toml"
DEFAULT_OPENJPEG_BIN = REPO_ROOT / ".build" / "openjpeg-2.5.4" / "bin" / "opj_decompress"
CORPUS_ROOTS = {
    "openjpeg-input": REPO_ROOT / "openjpeg-data",
    "iso15444-4": REPO_ROOT / "external" / "iso15444-4",
}
ISO_ATTACHMENT = {
    "edition": "ISO/IEC 15444-4:2024",
    "source": "https://standards.iso.org/iso-iec/15444/-4/ed-4/en/electronic_insert.zip",
    "archive_sha256": "ac04b52e1fe38404912036c14f215099ea9a785f38644fbe76ae8f3d1523c86d",
}
CODESTREAM_SUFFIXES = frozenset({".j2c", ".j2k", ".jp2", ".jpc", ".jhc", ".jph"})
CASE_STATUSES = (
    "pass",
    "bug",
    "known_unsupported",
    "oracle_failure",
    "invalid_fixture",
    "out_of_scope",
)


@dataclass(frozen=True, slots=True)
class PgxImage:
    width: int
    height: int
    precision: int
    is_signed: bool
    samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class OpenJPEGCase:
    path: str
    status: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ComponentTolerance:
    peak: int
    mse: float


@dataclass(frozen=True, slots=True)
class IsoCaseSpec:
    profile: int
    number: int
    reduction: int
    tolerances: tuple[ComponentTolerance, ...]

    @property
    def case_id(self) -> str:
        return f"p{self.profile}_{self.number:02d}"

    @property
    def codestream(self) -> str:
        return f"codestreams_profile{self.profile}/{self.case_id}.j2k"

    @property
    def reference_directory(self) -> str:
        return f"reference_class1_profile{self.profile}"


def _tolerances(*values: tuple[int, float]) -> tuple[ComponentTolerance, ...]:
    return tuple(ComponentTolerance(peak, mse) for peak, mse in values)


# Normative ISO/IEC 15444-4:2024 Tables C.6 and C.7.
ISO_CLASS1_CASES = (
    IsoCaseSpec(0, 1, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 2, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 3, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 4, 0, _tolerances((5, 0.776), (4, 0.626), (6, 1.070))),
    IsoCaseSpec(0, 5, 0, _tolerances((2, 0.319), (2, 0.323), (2, 0.317), (0, 0))),
    IsoCaseSpec(0, 6, 0, _tolerances((635, 11287), (403, 6124), (378, 3968), (0, 0))),
    IsoCaseSpec(0, 7, 0, _tolerances((0, 0), (0, 0), (0, 0))),
    IsoCaseSpec(0, 8, 1, _tolerances((0, 0), (0, 0), (0, 0))),
    IsoCaseSpec(0, 9, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 10, 0, _tolerances((0, 0), (0, 0), (0, 0))),
    IsoCaseSpec(0, 11, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 12, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 13, 0, _tolerances((0, 0), (0, 0), (0, 0), (0, 0))),
    IsoCaseSpec(0, 14, 0, _tolerances((0, 0), (0, 0), (0, 0))),
    IsoCaseSpec(0, 15, 0, _tolerances((0, 0))),
    IsoCaseSpec(0, 16, 0, _tolerances((0, 0))),
    IsoCaseSpec(1, 1, 0, _tolerances((0, 0))),
    IsoCaseSpec(1, 2, 0, _tolerances((5, 0.765), (4, 0.616), (6, 1.051))),
    IsoCaseSpec(1, 3, 0, _tolerances((2, 0.311), (2, 0.280), (1, 0.267), (0, 0))),
    IsoCaseSpec(1, 4, 0, _tolerances((624, 3080))),
    IsoCaseSpec(1, 5, 0, _tolerances((40, 8.458), (40, 9.716), (40, 10.154))),
    IsoCaseSpec(1, 6, 0, _tolerances((2, 0.600), (2, 0.600), (2, 0.600))),
    IsoCaseSpec(1, 7, 0, _tolerances((0, 0), (0, 0))),
)


@dataclass(frozen=True, slots=True)
class ApplicabilityRule:
    pattern: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class CorpusConfig:
    source_root: str
    include: tuple[str, ...]
    rules: tuple[ApplicabilityRule, ...]


def load_applicability_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, CorpusConfig]:
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    if document.get("schema_version") != 1:
        raise ValueError(f"unsupported applicability manifest schema in {path}")

    result: dict[str, CorpusConfig] = {}
    for name, raw_config in document.get("corpora", {}).items():
        rules: list[ApplicabilityRule] = []
        for raw_rule in raw_config.get("rules", []):
            status = raw_rule["status"]
            if status not in {"known_unsupported", "out_of_scope"}:
                raise ValueError(f"invalid applicability status {status!r} for {name}")
            rules.append(
                ApplicabilityRule(
                    pattern=raw_rule["pattern"],
                    status=status,
                    reason=raw_rule["reason"],
                )
            )
        result[name] = CorpusConfig(
            source_root=raw_config.get("source_root", "."),
            include=tuple(raw_config.get("include", ["**/*"])),
            rules=tuple(rules),
        )
    return result


def discover_fixtures(data_root: Path, config: CorpusConfig) -> list[str]:
    source_root = data_root / config.source_root
    if not source_root.is_dir():
        raise FileNotFoundError(f"corpus source root does not exist: {source_root}")
    fixtures = {
        str(path.relative_to(data_root))
        for pattern in config.include
        for path in source_root.glob(pattern)
        if path.is_file() and path.suffix.lower() in CODESTREAM_SUFFIXES
    }
    return sorted(fixtures)


def discover_openjpeg_input_fixtures(data_root: Path) -> list[str]:
    """Compatibility wrapper used by downstream tooling."""
    config = load_applicability_manifest()["openjpeg-input"]
    return discover_fixtures(data_root, config)


def resolve_data_root(corpus: str, data_root: str | Path | None) -> Path:
    path = Path(data_root) if data_root is not None else CORPUS_ROOTS[corpus]
    if not path.is_dir():
        hint = (
            "initialize the openjpeg-data submodule"
            if corpus == "openjpeg-input"
            else "attach the separately obtained normative corpus"
        )
        raise FileNotFoundError(f"{corpus} root does not exist: {path} ({hint})")
    return path


def resolve_opj_decompress(bin_path: str | Path | None) -> Path:
    if bin_path is not None:
        path = Path(bin_path)
        if path.is_dir():
            path = path / "opj_decompress"
        if not path.is_file():
            raise FileNotFoundError(f"opj_decompress not found: {path}")
        return path
    if DEFAULT_OPENJPEG_BIN.is_file():
        return DEFAULT_OPENJPEG_BIN
    path_env = which("opj_decompress")
    if path_env:
        return Path(path_env)
    raise FileNotFoundError(
        f"opj_decompress not found; run tools/build_openjpeg.sh (expected {DEFAULT_OPENJPEG_BIN})"
    )


def read_pgx(path: Path) -> PgxImage:
    with path.open("rb") as handle:
        try:
            header = handle.readline().decode("ascii").strip().split()
        except UnicodeDecodeError as exc:
            raise ValueError(f"non-ASCII PGX header in {path}") from exc
        if len(header) == 5 and header[0] == "PG" and header[2][:1] in {"+", "-"}:
            header = [header[0], header[1], header[2][0], header[2][1:], *header[3:]]
        elif len(header) == 5 and header[0] == "PG" and header[2].isdigit():
            # The 2024 normative attachment omits "+" on several unsigned PGX files.
            header = [header[0], header[1], "+", *header[2:]]
        if len(header) != 6 or header[0] != "PG":
            raise ValueError(f"unsupported PGX header in {path}")
        endian, sign, precision, width, height = header[1:]
        if endian not in {"ML", "LM"}:
            raise ValueError(f"unsupported PGX endian marker {endian}")
        if sign not in {"+", "-"}:
            raise ValueError(f"unsupported PGX sign marker {sign}")
        try:
            precision_int = int(precision)
            width_int = int(width)
            height_int = int(height)
        except ValueError as exc:
            raise ValueError(f"invalid PGX dimensions or precision in {path}") from exc
        raw_data = handle.read()

    if precision_int <= 0 or width_int <= 0 or height_int <= 0:
        raise ValueError(f"non-positive PGX dimensions or precision in {path}")
    byte_count = (precision_int + 7) // 8
    expected_len = width_int * height_int * byte_count
    if len(raw_data) != expected_len:
        raise ValueError(
            f"unexpected PGX data length in {path}: got {len(raw_data)}, expected {expected_len}"
        )

    byte_order = "big" if endian == "ML" else "little"
    modulus = 1 << precision_int
    value_mask = modulus - 1
    sign_bit = 1 << (precision_int - 1)
    samples: list[int] = []
    for offset in range(0, len(raw_data), byte_count):
        value = int.from_bytes(raw_data[offset : offset + byte_count], byte_order) & value_mask
        if sign == "-" and value & sign_bit:
            value -= modulus
        samples.append(value)
    return PgxImage(width_int, height_int, precision_int, sign == "-", tuple(samples))


def decode_with_openjpeg(
    opj_decompress: Path,
    source: Path,
    output_stem: Path,
) -> list[PgxImage]:
    output = output_stem.with_suffix(".pgx")
    subprocess.run(
        [str(opj_decompress), "-i", str(source), "-o", str(output)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    component_files = sorted(output.parent.glob(f"{output.stem}_*.pgx"))
    if not component_files and output.exists():
        component_files = [output]
    if not component_files:
        raise ValueError("OpenJPEG produced no PGX components")
    return [read_pgx(path) for path in component_files]


def _native_samples(component: Any) -> tuple[int, ...]:
    samples = getattr(component, "samples", ())
    return tuple(samples) if samples else tuple(component.data)


def components_match(decoded: Any, oracle: list[PgxImage]) -> bool:
    if len(decoded.components) != len(oracle):
        return False
    for actual, expected in zip(decoded.components, oracle, strict=True):
        if (
            actual.width != expected.width
            or actual.height != expected.height
            or actual.precision != expected.precision
            or actual.is_signed != expected.is_signed
            or _native_samples(actual) != expected.samples
        ):
            return False
    return True


def component_error_metrics(
    actual_samples: tuple[int, ...],
    expected_samples: tuple[int, ...],
) -> tuple[int, float]:
    """Return peak absolute error and mean squared error."""
    if len(actual_samples) != len(expected_samples) or not expected_samples:
        raise ValueError("component sample counts differ or are empty")
    squared_error = 0
    peak_error = 0
    for actual, expected in zip(actual_samples, expected_samples, strict=True):
        error = abs(actual - expected)
        peak_error = max(peak_error, error)
        squared_error += error * error
    return peak_error, squared_error / len(expected_samples)


def iso_case_specs(formal_profile: int) -> tuple[IsoCaseSpec, ...]:
    if formal_profile not in {0, 1}:
        raise ValueError("Class 1 formal profile must be 0 or 1")
    # C.2.4.1 requires Profile 1 decoders to also pass all Profile 0 tests.
    return tuple(case for case in ISO_CLASS1_CASES if case.profile <= formal_profile)


def iso_reference_path(data_root: Path, spec: IsoCaseSpec, component: int) -> Path:
    filename = f"c1p{spec.profile}_{spec.number:02d}-{component}.pgx"
    return data_root / spec.reference_directory / filename


def discover_iso_class1_cases(data_root: Path, formal_profile: int) -> tuple[IsoCaseSpec, ...]:
    """Verify and return the exact normative Class 1 fixture inventory."""
    specs = iso_case_specs(formal_profile)
    missing: list[str] = []
    for spec in specs:
        if not (data_root / spec.codestream).is_file():
            missing.append(spec.codestream)
        for component in range(len(spec.tolerances)):
            reference = iso_reference_path(data_root, spec, component)
            if not reference.is_file():
                missing.append(str(reference.relative_to(data_root)))
    if missing:
        raise FileNotFoundError("missing normative fixture(s): " + ", ".join(missing))
    return specs


def _expected_component(
    data_root: Path,
    spec: IsoCaseSpec,
    component: int,
    tolerance: ComponentTolerance,
) -> tuple[PgxImage, dict[str, Any]]:
    reference_path = iso_reference_path(data_root, spec, component)
    reference = read_pgx(reference_path)
    return reference, {
        "component": component,
        "reference": str(reference_path.relative_to(data_root)),
        "expected": {
            "width": reference.width,
            "height": reference.height,
            "precision": reference.precision,
            "signed": reference.is_signed,
            "peak_threshold": tolerance.peak,
            "mse_threshold": tolerance.mse,
        },
        "metrics": {"peak_error": None, "mse": None},
        "status": "bug",
    }


def classify_iso_case(data_root: Path, spec: IsoCaseSpec) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": spec.case_id,
        "profile": spec.profile,
        "path": spec.codestream,
        "reduction": spec.reduction,
        "status": "bug",
        "reason": None,
        "components": [],
    }
    try:
        references_and_results = [
            _expected_component(data_root, spec, component, tolerance)
            for component, tolerance in enumerate(spec.tolerances)
        ]
    except (OSError, ValueError) as exc:
        case["status"] = "invalid_fixture"
        case["reason"] = str(exc)
        return case
    references = [item[0] for item in references_and_results]
    results = [item[1] for item in references_and_results]
    case["components"] = results

    if spec.reduction:
        case["reason"] = (
            "core_jpeg has no reduced-resolution decode API; "
            f"normative reduction={spec.reduction} was not executed"
        )
        for result in results:
            result["reason"] = case["reason"]
        return case

    try:
        source_bytes = (data_root / spec.codestream).read_bytes()
    except OSError as exc:
        case["status"] = "invalid_fixture"
        case["reason"] = str(exc)
        return case
    try:
        decoded = decode_jpx_image(source_bytes, apply_embedded_color=False)
    except JpegError as exc:
        case["reason"] = f"core_jpeg failed to decode: {exc}"
        return case
    except Exception as exc:  # pragma: no cover - defensive tooling path
        case["reason"] = f"unexpected exception: {type(exc).__name__}: {exc}"
        return case

    native_components = getattr(decoded, "native_components", ())
    native_by_index = {component.index: component for component in native_components}
    failures: list[str] = []
    for component_index, (reference, tolerance, result) in enumerate(
        zip(references, spec.tolerances, results, strict=True),
    ):
        actual = native_by_index.get(component_index)
        if actual is None:
            reason = f"required native component {component_index} was not produced"
            result["reason"] = reason
            failures.append(reason)
            continue
        metadata_matches = (
            actual.width == reference.width
            and actual.height == reference.height
            and actual.precision == reference.precision
            and actual.is_signed == reference.is_signed
        )
        if not metadata_matches:
            reason = (
                f"component {component_index} metadata mismatch: got "
                f"{actual.width}x{actual.height} {actual.precision}-bit "
                f"{'signed' if actual.is_signed else 'unsigned'}"
            )
            result["reason"] = reason
            failures.append(reason)
            continue
        try:
            peak_error, mse = component_error_metrics(_native_samples(actual), reference.samples)
        except ValueError as exc:
            reason = f"component {component_index}: {exc}"
            result["reason"] = reason
            failures.append(reason)
            continue
        result["metrics"] = {"peak_error": peak_error, "mse": mse}
        if peak_error <= tolerance.peak and mse <= tolerance.mse:
            result["status"] = "pass"
        else:
            reason = (
                f"component {component_index} exceeds tolerance: "
                f"peak {peak_error}>{tolerance.peak} or MSE {mse}>{tolerance.mse}"
            )
            result["reason"] = reason
            failures.append(reason)

    if failures:
        case["reason"] = "; ".join(failures)
    else:
        case["status"] = "pass"
    return case


def applicable_status(relative_path: str, config: CorpusConfig) -> tuple[str, str] | None:
    for rule in config.rules:
        if fnmatch.fnmatchcase(relative_path, rule.pattern):
            return rule.status, rule.reason
    return None


def classify_openjpeg_case(
    data_root: Path,
    opj_decompress: Path,
    relative_path: str,
    *,
    config: CorpusConfig | None = None,
) -> OpenJPEGCase:
    config = config or load_applicability_manifest()["openjpeg-input"]
    applicability = applicable_status(relative_path, config)
    if applicability is not None:
        status, reason = applicability
        return OpenJPEGCase(relative_path, status, reason)

    source = data_root / relative_path
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        return OpenJPEGCase(relative_path, "invalid_fixture", str(exc))
    if source.suffix.lower() not in CODESTREAM_SUFFIXES or not source_bytes:
        return OpenJPEGCase(relative_path, "invalid_fixture", "empty or unsupported fixture")

    with tempfile.TemporaryDirectory(prefix="core-jpeg-openjpeg-") as tmpdir:
        try:
            oracle = decode_with_openjpeg(opj_decompress, source, Path(tmpdir) / "oracle")
        except subprocess.CalledProcessError as exc:
            reason = exc.stderr.decode("utf-8", "replace").strip() if exc.stderr else ""
            return OpenJPEGCase(
                relative_path,
                "oracle_failure",
                reason or f"OpenJPEG exited with status {exc.returncode}",
            )
        except (OSError, ValueError) as exc:
            return OpenJPEGCase(relative_path, "oracle_failure", str(exc))

    try:
        decoded = decode_jpx_image(source_bytes, apply_embedded_color=False)
    except JpegError as exc:
        return OpenJPEGCase(relative_path, "bug", f"core_jpeg failed to decode: {exc}")
    except Exception as exc:  # pragma: no cover - defensive tooling path
        return OpenJPEGCase(
            relative_path,
            "bug",
            f"unexpected exception: {type(exc).__name__}: {exc}",
        )
    if not components_match(decoded, oracle):
        return OpenJPEGCase(relative_path, "bug", "native component output mismatch")
    return OpenJPEGCase(relative_path, "pass")


def _report(
    corpus: str,
    data_root: Path,
    opj_decompress: Path,
    cases: list[OpenJPEGCase],
) -> dict[str, Any]:
    counts = Counter(case.status for case in cases)
    return {
        "schema_version": 1,
        "corpus": corpus,
        "data_root": str(data_root),
        "oracle": {"implementation": "OpenJPEG", "version": "2.5.4", "path": str(opj_decompress)},
        "counts": {status: counts[status] for status in CASE_STATUSES},
        "total": len(cases),
        "cases": [asdict(case) for case in cases],
    }


def run_conformance(
    corpus: str,
    data_root: Path,
    opj_decompress: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    limit: int | None = None,
    fail_fast: bool = False,
) -> tuple[int, dict[str, Any]]:
    config = load_applicability_manifest(manifest_path)[corpus]
    fixtures = discover_fixtures(data_root, config)
    if limit is not None:
        fixtures = fixtures[:limit]
    cases: list[OpenJPEGCase] = []
    for relative_path in fixtures:
        case = classify_openjpeg_case(
            data_root,
            opj_decompress,
            relative_path,
            config=config,
        )
        cases.append(case)
        if fail_fast and case.status == "bug":
            break
    report = _report(corpus, data_root, opj_decompress, cases)
    return (1 if report["counts"]["bug"] else 0), report


def _iso_fixture_inventory_sha256(data_root: Path, specs: tuple[IsoCaseSpec, ...]) -> str:
    digest = hashlib.sha256()
    paths: list[Path] = []
    for spec in specs:
        paths.append(data_root / spec.codestream)
        paths.extend(
            iso_reference_path(data_root, spec, component)
            for component in range(len(spec.tolerances))
        )
    for path in sorted(paths):
        relative = str(path.relative_to(data_root)).encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def run_iso_conformance(
    data_root: Path,
    *,
    formal_profile: int = 1,
    limit: int | None = None,
    fail_fast: bool = False,
) -> tuple[int, dict[str, Any]]:
    specs = discover_iso_class1_cases(data_root, formal_profile)
    inventory_sha256 = _iso_fixture_inventory_sha256(data_root, specs)
    if limit is not None:
        specs = specs[:limit]
    cases: list[dict[str, Any]] = []
    for spec in specs:
        case = classify_iso_case(data_root, spec)
        cases.append(case)
        if fail_fast and case["status"] == "bug":
            break
    counts = Counter(case["status"] for case in cases)
    report = {
        "schema_version": 2,
        "corpus": "iso15444-4",
        "suite": f"Class 1 Profile {formal_profile}",
        "formal_profile": formal_profile,
        "data_root": str(data_root),
        "attachment": ISO_ATTACHMENT,
        "fixture_inventory_sha256": inventory_sha256,
        "oracle": None,
        "counts": {status: counts[status] for status in CASE_STATUSES},
        "total": len(cases),
        "cases": cases,
    }
    return (1 if counts["bug"] or counts["invalid_fixture"] else 0), report


def iso_case_status_groups(report: dict[str, Any]) -> dict[str, list[str]]:
    groups = {status: [] for status in CASE_STATUSES}
    cases = report["cases"]
    if isinstance(cases, dict):
        for status, case_ids in cases.items():
            groups[status] = sorted(case_ids)
        return groups
    for case in cases:
        groups[case["status"]].append(case["id"])
    return {status: sorted(case_ids) for status, case_ids in groups.items()}


def compare_iso_baseline(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Return human-readable regressions; empty means the run matches the baseline."""
    mismatches: list[str] = []
    report_attachment = report.get("attachment") or {}
    baseline_attachment = baseline.get("attachment") or {}
    report_sha = report_attachment.get("archive_sha256")
    baseline_sha = baseline_attachment.get("archive_sha256")
    if report_sha != baseline_sha:
        mismatches.append(
            f"attachment archive sha256 mismatch: got {report_sha}, baseline {baseline_sha}",
        )
    report_inventory = report.get("fixture_inventory_sha256")
    baseline_inventory = baseline.get("fixture_inventory_sha256")
    if report_inventory != baseline_inventory:
        mismatches.append(
            "fixture inventory sha256 mismatch: "
            f"got {report_inventory}, baseline {baseline_inventory}",
        )
    report_groups = iso_case_status_groups(report)
    baseline_groups = iso_case_status_groups(baseline)
    for status in CASE_STATUSES:
        got = report_groups[status]
        expected = baseline_groups[status]
        if got == expected:
            continue
        missing = sorted(set(expected) - set(got))
        unexpected = sorted(set(got) - set(expected))
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        mismatches.append(f"{status} cases differ ({', '.join(details)})")
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser(prog="openjpeg-conformance")
    parser.add_argument("--corpus", required=True, choices=sorted(CORPUS_ROOTS))
    parser.add_argument("--data-root", type=Path, help="Override the selected corpus root")
    parser.add_argument("--openjpeg-bin", help="Path to opj_decompress or its directory")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--profile",
        type=int,
        choices=(0, 1),
        default=1,
        help="Formal ISO Class 1 profile (Profile 1 includes Profile 0)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--baseline",
        type=Path,
        help=(
            "For iso15444-4, compare results to this baseline and exit non-zero only on regressions"
        ),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Write the JSON report to this path as well as stdout",
    )
    args = parser.parse_args()

    data_root = resolve_data_root(args.corpus, args.data_root)
    if args.corpus == "iso15444-4":
        exit_code, report = run_iso_conformance(
            data_root,
            formal_profile=args.profile,
            limit=args.limit,
            fail_fast=args.fail_fast,
        )
        if args.baseline is not None:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            mismatches = compare_iso_baseline(report, baseline)
            if mismatches:
                print("ISO baseline regressions:", flush=True)
                for mismatch in mismatches:
                    print(f"  - {mismatch}", flush=True)
                exit_code = 1
            else:
                exit_code = 0
    else:
        if args.baseline is not None:
            parser.error("--baseline is only supported with --corpus iso15444-4")
        opj_decompress = resolve_opj_decompress(args.openjpeg_bin)
        exit_code, report = run_conformance(
            args.corpus,
            data_root,
            opj_decompress,
            manifest_path=args.manifest,
            limit=args.limit,
            fail_fast=args.fail_fast,
        )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
