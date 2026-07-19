from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tools import openjpeg_conformance as harness


def write_pgx(
    path: Path,
    *,
    endian: str,
    sign: str,
    precision: int,
    width: int,
    height: int,
    values: list[int],
) -> None:
    byte_order = "big" if endian == "ML" else "little"
    byte_count = (precision + 7) // 8
    modulus = 1 << precision
    payload = b"".join((value % modulus).to_bytes(byte_count, byte_order) for value in values)
    path.write_bytes(f"PG {endian} {sign} {precision} {width} {height}\n".encode() + payload)


@dataclass
class Component:
    width: int
    height: int
    precision: int
    is_signed: bool
    data: bytes = b""
    samples: tuple[int, ...] = ()
    index: int = 0


@dataclass
class Image:
    components: tuple[Component, ...]
    native_components: tuple[Component, ...] = ()


def test_read_pgx_preserves_unsigned_native_samples(tmp_path: Path) -> None:
    path = tmp_path / "unsigned.pgx"
    write_pgx(
        path,
        endian="ML",
        sign="+",
        precision=12,
        width=2,
        height=1,
        values=[0, 4095],
    )

    image = harness.read_pgx(path)

    assert image.samples == (0, 4095)
    assert image.precision == 12
    assert not image.is_signed


def test_read_pgx_accepts_normative_compact_sign_and_depth(tmp_path: Path) -> None:
    path = tmp_path / "normative.pgx"
    path.write_bytes(b"PG ML +8 2 1\n\x00\xff")

    image = harness.read_pgx(path)

    assert image.samples == (0, 255)
    assert image.precision == 8


def test_read_pgx_accepts_normative_omitted_unsigned_sign(tmp_path: Path) -> None:
    path = tmp_path / "normative-unsigned.pgx"
    path.write_bytes(b"PG ML 8 2 1\n\x00\xff")

    image = harness.read_pgx(path)

    assert image.samples == (0, 255)
    assert not image.is_signed


def test_read_pgx_preserves_signed_little_endian_samples(tmp_path: Path) -> None:
    path = tmp_path / "signed.pgx"
    write_pgx(
        path,
        endian="LM",
        sign="-",
        precision=10,
        width=3,
        height=1,
        values=[-512, -1, 511],
    )

    assert harness.read_pgx(path).samples == (-512, -1, 511)


def test_read_pgx_rejects_truncated_payload(tmp_path: Path) -> None:
    path = tmp_path / "truncated.pgx"
    path.write_bytes(b"PG ML + 16 2 1\n\x00\x01")

    with pytest.raises(ValueError, match="data length"):
        harness.read_pgx(path)


def test_components_match_requires_exact_count_and_native_samples() -> None:
    expected = harness.PgxImage(1, 1, 12, False, (1024,))
    component = Component(1, 1, 12, False, data=b"\xff", samples=(1024,))

    assert harness.components_match(Image((component,)), [expected])
    assert not harness.components_match(Image((component, component)), [expected])


def test_manifest_discovery_and_out_of_scope_classification(tmp_path: Path) -> None:
    (tmp_path / "input" / "htj2k").mkdir(parents=True)
    (tmp_path / "input" / "regular.j2k").write_bytes(b"regular")
    (tmp_path / "input" / "htj2k" / "fast.jph").write_bytes(b"ht")
    config = harness.CorpusConfig(
        source_root="input",
        include=("**/*",),
        rules=(
            harness.ApplicabilityRule(
                pattern="input/htj2k/*",
                status="out_of_scope",
                reason="Part 15",
            ),
        ),
    )

    assert harness.discover_fixtures(tmp_path, config) == [
        "input/htj2k/fast.jph",
        "input/regular.j2k",
    ]
    case = harness.classify_openjpeg_case(
        tmp_path,
        tmp_path / "unused-opj",
        "input/htj2k/fast.jph",
        config=config,
    )
    assert case.status == "out_of_scope"


def test_classification_distinguishes_invalid_fixture_and_oracle_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = harness.CorpusConfig(".", ("**/*",), ())
    missing = harness.classify_openjpeg_case(
        tmp_path,
        tmp_path / "opj",
        "missing.j2k",
        config=config,
    )
    assert missing.status == "invalid_fixture"

    source = tmp_path / "sample.j2k"
    source.write_bytes(b"not-empty")

    def fail_oracle(*args: object, **kwargs: object) -> list[harness.PgxImage]:
        raise ValueError("bad oracle output")

    monkeypatch.setattr(harness, "decode_with_openjpeg", fail_oracle)
    failed = harness.classify_openjpeg_case(
        tmp_path,
        tmp_path / "opj",
        "sample.j2k",
        config=config,
    )
    assert failed.status == "oracle_failure"
    assert failed.reason == "bad oracle output"


def test_classification_reports_pass_and_bug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "sample.j2k"
    source.write_bytes(b"codestream")
    expected = harness.PgxImage(1, 1, 12, False, (2048,))
    monkeypatch.setattr(harness, "decode_with_openjpeg", lambda *args: [expected])
    monkeypatch.setattr(
        harness,
        "decode_jpx_image",
        lambda *args, **kwargs: Image((Component(1, 1, 12, False, samples=(2048,)),)),
    )
    config = harness.CorpusConfig(".", ("**/*",), ())

    passed = harness.classify_openjpeg_case(
        tmp_path,
        tmp_path / "opj",
        "sample.j2k",
        config=config,
    )
    assert passed.status == "pass"

    monkeypatch.setattr(
        harness,
        "decode_jpx_image",
        lambda *args, **kwargs: Image((Component(1, 1, 12, False, samples=(0,)),)),
    )
    bug = harness.classify_openjpeg_case(
        tmp_path,
        tmp_path / "opj",
        "sample.j2k",
        config=config,
    )
    assert bug.status == "bug"


def test_component_error_metrics_gate_peak_and_mse() -> None:
    assert harness.component_error_metrics((10, 12, 14), (10, 10, 10)) == (
        4,
        pytest.approx(20 / 3),
    )
    assert harness.component_error_metrics((7,), (7,)) == (0, 0.0)
    with pytest.raises(ValueError, match="sample counts"):
        harness.component_error_metrics((1,), (1, 2))


def test_iso_case_mapping_and_profile_aggregation() -> None:
    profile0 = harness.iso_case_specs(0)
    profile1 = harness.iso_case_specs(1)

    assert len(profile0) == 16
    assert len(profile1) == 23
    assert profile0[0].codestream == "codestreams_profile0/p0_01.j2k"
    assert profile0[-1].case_id == "p0_16"
    assert profile1[-1].codestream == "codestreams_profile1/p1_07.j2k"
    assert profile0[7].reduction == 1
    assert all(case.reduction == 0 for case in profile1 if case.case_id != "p0_08")


def test_iso_classification_uses_native_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = harness.IsoCaseSpec(0, 1, 0, harness._tolerances((0, 0)))
    source = tmp_path / spec.codestream
    source.parent.mkdir(parents=True)
    source.write_bytes(b"codestream")
    reference = harness.iso_reference_path(tmp_path, spec, 0)
    reference.parent.mkdir()
    write_pgx(
        reference,
        endian="ML",
        sign="+",
        precision=8,
        width=1,
        height=1,
        values=[42],
    )
    display = Component(1, 1, 8, False, samples=(0,))
    native = Component(1, 1, 8, False, samples=(42,))
    monkeypatch.setattr(
        harness,
        "decode_jpx_image",
        lambda *args, **kwargs: Image((display,), (native,)),
    )

    case = harness.classify_iso_case(tmp_path, spec)

    assert case["status"] == "pass"
    assert case["components"][0]["metrics"] == {"peak_error": 0, "mse": 0.0}


def test_iso_reduction_is_deterministic_bug_without_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = harness.IsoCaseSpec(0, 8, 1, harness._tolerances((0, 0)))
    reference = harness.iso_reference_path(tmp_path, spec, 0)
    reference.parent.mkdir(parents=True)
    write_pgx(
        reference,
        endian="ML",
        sign="-",
        precision=12,
        width=1,
        height=1,
        values=[0],
    )
    monkeypatch.setattr(
        harness,
        "decode_jpx_image",
        lambda *args, **kwargs: pytest.fail("reduced case must not be decoded at full resolution"),
    )

    case = harness.classify_iso_case(tmp_path, spec)

    assert case["status"] == "bug"
    assert case["reduction"] == 1
    assert "no reduced-resolution decode API" in case["reason"]


def test_iso_run_aggregates_exact_statuses_without_openjpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = (
        harness.IsoCaseSpec(0, 1, 0, harness._tolerances((0, 0))),
        harness.IsoCaseSpec(1, 1, 0, harness._tolerances((0, 0))),
    )
    monkeypatch.setattr(harness, "discover_iso_class1_cases", lambda *args: specs)
    monkeypatch.setattr(harness, "_iso_fixture_inventory_sha256", lambda *args: "abc")
    monkeypatch.setattr(
        harness,
        "classify_iso_case",
        lambda root, spec: {
            "id": spec.case_id,
            "status": "pass" if spec.profile == 0 else "bug",
        },
    )

    exit_code, report = harness.run_iso_conformance(tmp_path, formal_profile=1)

    assert exit_code == 1
    assert report["oracle"] is None
    assert report["attachment"]["edition"] == "ISO/IEC 15444-4:2024"
    assert report["counts"]["pass"] == 1
    assert report["counts"]["bug"] == 1


def test_compare_iso_baseline_accepts_known_failure_set() -> None:
    report = {
        "attachment": harness.ISO_ATTACHMENT,
        "fixture_inventory_sha256": "abc",
        "cases": [
            {"id": "p0_01", "status": "pass"},
            {"id": "p0_02", "status": "bug"},
        ],
    }
    baseline = {
        "attachment": harness.ISO_ATTACHMENT,
        "fixture_inventory_sha256": "abc",
        "cases": {
            "pass": ["p0_01"],
            "bug": ["p0_02"],
            "known_unsupported": [],
            "oracle_failure": [],
            "invalid_fixture": [],
            "out_of_scope": [],
        },
    }

    assert harness.compare_iso_baseline(report, baseline) == []


def test_compare_iso_baseline_allows_improvements_but_flags_regressions() -> None:
    baseline = {
        "attachment": harness.ISO_ATTACHMENT,
        "fixture_inventory_sha256": "abc",
        "cases": {
            "pass": ["p0_01"],
            "bug": ["p0_02", "p1_01"],
        },
    }
    improved = {
        "attachment": harness.ISO_ATTACHMENT,
        "fixture_inventory_sha256": "abc",
        "cases": [
            {"id": "p0_01", "status": "pass"},
            {"id": "p0_02", "status": "bug"},
            {"id": "p1_01", "status": "pass"},
        ],
    }
    regressed = {
        "attachment": harness.ISO_ATTACHMENT,
        "fixture_inventory_sha256": "changed",
        "cases": [
            {"id": "p0_01", "status": "bug"},
            {"id": "p0_02", "status": "pass"},
            {"id": "p1_01", "status": "bug"},
            {"id": "p1_99", "status": "bug"},
        ],
    }

    assert harness.compare_iso_baseline(improved, baseline) == []

    mismatches = harness.compare_iso_baseline(regressed, baseline)
    assert any("fixture inventory sha256 mismatch" in item for item in mismatches)
    assert any("lost baseline passes" in item for item in mismatches)
    assert any("new bug cases" in item for item in mismatches)
