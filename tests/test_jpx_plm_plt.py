from __future__ import annotations

import pytest

from core_jpeg.impl.codecs.jpx.codestream import JpxImage
from core_jpeg.impl.codecs.jpx.markers import parse_plm, read_tile_part_header
from core_jpeg.impl.codecs.jpx.structures import BitStream
from core_jpeg.impl.errors import JpegParseError


def _encode_packet_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("packet length must be non-negative")
    chunks: list[int] = []
    while True:
        chunks.append(value & 0x7F)
        value >>= 7
        if value == 0:
            break
    chunks.reverse()
    for index in range(len(chunks) - 1):
        chunks[index] |= 0x80
    return bytes(chunks)


def _sot_tile_part(
    *,
    tile_index: int = 0,
    tile_part_index: int = 0,
    tile_part_count: int = 1,
    payload: bytes,
    plt_lengths: list[int] | None = None,
) -> bytes:
    header = bytearray(b"\xff\x90\x00\x0a")
    header.extend(tile_index.to_bytes(2, "big"))
    # Psot filled after the rest of the tile-part is known.
    psot_offset = len(header)
    header.extend((0).to_bytes(4, "big"))
    header.append(tile_part_index)
    header.append(tile_part_count)
    if plt_lengths is not None:
        encoded = b"".join(_encode_packet_length(length) for length in plt_lengths)
        plt = bytes([0]) + encoded
        header.extend(b"\xff\x58")
        header.extend((len(plt) + 2).to_bytes(2, "big"))
        header.extend(plt)
    header.extend(b"\xff\x93")
    tile_part = bytes(header) + payload
    psot = len(tile_part)
    return tile_part[:psot_offset] + psot.to_bytes(4, "big") + tile_part[psot_offset + 4 :]


def _image_for_tile_part() -> JpxImage:
    image = JpxImage()
    image.width = 1
    image.height = 1
    image.components = 1
    image.x_end = 1
    image.y_end = 1
    image.tile_width = 1
    image.tile_height = 1
    image.tiles_cols = 1
    image.tiles_rows = 1
    image.levels = 0
    image.codeblock_w = 2
    image.codeblock_h = 2
    image.prog_order = 0
    image.num_layers = 1
    image.reversible = True
    image.quant_guard_bits = 2
    image.quant_style = 0
    image.quant_steps = [[(0, 8)]]
    image.components_data = [{"precision": 8, "is_signed": False, "h_sep": 1, "v_sep": 1}]
    return image


def test_parse_plm_keeps_per_tile_part_blocks() -> None:
    image = JpxImage()
    block0 = _encode_packet_length(7)
    block1 = _encode_packet_length(3) + _encode_packet_length(4)
    payload = bytes([0, len(block0)]) + block0 + bytes([len(block1)]) + block1
    segment = (len(payload) + 2).to_bytes(2, "big") + payload
    parse_plm(image, BitStream(segment))

    assert image.plm_packet_lengths == [[7], [3, 4]]


def test_plm_rejects_tile_part_payload_length_mismatch() -> None:
    image = _image_for_tile_part()
    image.plm_packet_lengths = [[8]]
    payload = b"\x00"
    tile_part = _sot_tile_part(payload=payload)
    # read_tile_part_header expects the stream positioned after the SOT marker.
    br = BitStream(tile_part)
    assert br.read_u16() == 0xFF90

    with pytest.raises(
        JpegParseError,
        match="JPX PLM packet lengths do not match tile-part payload",
    ):
        read_tile_part_header(image, br, data_len=len(tile_part) + 2)


def test_plm_accepts_matching_tile_part_and_advances_index() -> None:
    image = _image_for_tile_part()
    payload = b"\x00"
    image.plm_packet_lengths = [[len(payload)], [len(payload)]]
    tile_part = _sot_tile_part(payload=payload)
    br = BitStream(tile_part)
    assert br.read_u16() == 0xFF90

    header = read_tile_part_header(image, br, data_len=len(tile_part) + 2)

    assert header.payload_end - header.payload_start == len(payload)
    assert image.plm_consume_index == 1


def test_plt_includes_sop_bytes_in_packet_length_sum() -> None:
    image = _image_for_tile_part()
    image.packet_uses_sop = True
    sop_packet = b"\xff\x91\x00\x04\x00\x00\x00"
    tile_part = _sot_tile_part(payload=sop_packet, plt_lengths=[len(sop_packet)])
    br = BitStream(tile_part)
    assert br.read_u16() == 0xFF90

    header = read_tile_part_header(image, br, data_len=len(tile_part) + 2)

    assert header.packet_lengths == (len(sop_packet),)
    assert header.payload_end - header.payload_start == len(sop_packet)


def test_plt_rejects_sum_mismatch_when_sop_bytes_omitted() -> None:
    image = _image_for_tile_part()
    image.packet_uses_sop = True
    sop_packet = b"\xff\x91\x00\x04\x00\x00\x00"
    tile_part = _sot_tile_part(payload=sop_packet, plt_lengths=[1])
    br = BitStream(tile_part)
    assert br.read_u16() == 0xFF90

    with pytest.raises(
        JpegParseError,
        match="JPX PLT packet lengths do not match tile-part payload",
    ):
        read_tile_part_header(image, br, data_len=len(tile_part) + 2)


def test_plm_and_plt_must_agree() -> None:
    image = _image_for_tile_part()
    payload = b"\x00\x00\x00\x00\x00"
    image.plm_packet_lengths = [[2, 3]]
    tile_part = _sot_tile_part(payload=payload, plt_lengths=[1, 4])
    br = BitStream(tile_part)
    assert br.read_u16() == 0xFF90

    with pytest.raises(JpegParseError, match="JPX PLM and PLT packet lengths disagree"):
        read_tile_part_header(image, br, data_len=len(tile_part) + 2)
