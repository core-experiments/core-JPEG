import pytest

from core_jpeg import JpegParseError, JpegUnsupportedError
from core_jpeg.impl.codecs.jpx.boxes import Jp2Parser
from core_jpeg.impl.codecs.jpx.output import native_sample_value


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def image_header(*, bits_per_component: int = 7) -> bytes:
    return (
        (2).to_bytes(4, "big")
        + (3).to_bytes(4, "big")
        + (1).to_bytes(2, "big")
        + bytes((bits_per_component, 7, 0, 0))
    )


def jp2_file(*header_children: bytes, trailing: bytes = b"") -> bytes:
    header = box(b"ihdr", image_header()) + box(b"colr", b"\x01\x00\x00\x00\x00\x00\x11")
    header += b"".join(header_children)
    return (
        box(b"jP  ", b"\r\n\x87\n")
        + box(b"ftyp", b"jp2 \x00\x00\x00\x00jp2 ")
        + box(b"jp2h", header)
        + box(b"jp2c", b"\xff\x4f\xff\xd9")
        + trailing
    )


def test_parser_reads_resolution_and_boxes_after_codestream() -> None:
    resolution = (
        (300).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (150).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + b"\x00\x01"
    )
    parsed = Jp2Parser(
        jp2_file(box(b"res ", box(b"resc", resolution)), trailing=box(b"free")),
    ).parse()

    assert parsed.capture_resolution is not None
    assert parsed.capture_resolution.vertical == 300
    assert parsed.capture_resolution.horizontal == 1500


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            box(b"ftyp", b"jp2 \x00\x00\x00\x00jp2 ") + box(b"jP  ", b"\r\n\x87\n"),
            "signature box must be first",
        ),
        (jp2_file(trailing=box(b"jp2c", b"x")), "duplicate JP2 codestream"),
        (jp2_file(trailing=b"\x00"), "truncated JP2 box header"),
    ],
)
def test_parser_enforces_top_level_structure(data: bytes, message: str) -> None:
    with pytest.raises(JpegParseError, match=message):
        Jp2Parser(data).parse()


def test_multiple_color_specifications_use_precedence() -> None:
    header = (
        box(b"ihdr", image_header())
        + box(b"colr", b"\x01\x01\x00\x00\x00\x00\x11")
        + box(b"colr", b"\x01\x00\x00\x00\x00\x00\x10")
    )
    data = (
        box(b"jP  ", b"\r\n\x87\n")
        + box(b"ftyp", b"jp2 \x00\x00\x00\x00jp2 ")
        + box(b"jp2h", header)
        + box(b"jp2c", b"\xff\x4f")
    )
    parsed = Jp2Parser(data).parse()

    assert len(parsed.color_specifications) == 2
    assert parsed.color_specification is not None
    assert parsed.color_specification.enum_color_space == 16


def test_unrestricted_icc_method_is_rejected() -> None:
    header = box(b"ihdr", image_header()) + box(b"colr", b"\x03\x00\x00profile")
    data = (
        box(b"jP  ", b"\r\n\x87\n")
        + box(b"ftyp", b"jp2 \x00\x00\x00\x00jp2 ")
        + box(b"jp2h", header)
        + box(b"jp2c", b"\xff\x4f")
    )

    with pytest.raises(JpegUnsupportedError, match="unrestricted ICC"):
        Jp2Parser(data).parse()


def test_component_mapping_allows_general_palette_column_order() -> None:
    palette = box(b"pclr", b"\x00\x02\x02\x07\x07\x00\x01\x02\x03")
    mapping = box(b"cmap", b"\x00\x00\x01\x01\x00\x00\x01\x00")
    parsed = Jp2Parser(jp2_file(palette, mapping)).parse()

    assert [item.palette_column for item in parsed.component_mapping] == [1, 0]


def test_native_samples_preserve_signed_and_high_precision_values() -> None:
    assert native_sample_value(-17, precision=12, is_signed=True) == -17
    assert native_sample_value(123456, precision=38, is_signed=True) == 123456
    assert native_sample_value(-2048, precision=12, is_signed=False) == 0
    assert native_sample_value(2047, precision=12, is_signed=False) == 4095
