from __future__ import annotations

from core_jpeg.impl.codecs.jpx.codestream import JpxImage
from core_jpeg.impl.codecs.jpx.params import JpxCodingParams


def _sop(sequence: int) -> bytes:
    return b"\xff\x91\x00\x04" + sequence.to_bytes(2, "big")


def _empty_sop_packet(sequence: int = 0) -> bytes:
    # Empty packet header (zero inclusion bit, byte-aligned) with a leading SOP.
    return _sop(sequence) + b"\x00"


def _image_with_sop_layers(num_layers: int = 2) -> tuple[JpxImage, JpxCodingParams]:
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
    image.num_layers = num_layers
    image.packet_uses_sop = True
    image.reversible = True
    image.quant_guard_bits = 2
    image.quant_style = 0
    # Reversible no-quantization exponent for LL.
    image.quant_steps = [[(0, 8)]]
    image.components_data = [
        {
            "precision": 8,
            "is_signed": False,
            "h_sep": 1,
            "v_sep": 1,
        }
    ]
    image.initialize_tile_slots()
    params = image.coding_params()
    return image, params


def test_sop_sequence_restarts_for_each_tile_part() -> None:
    image, params = _image_with_sop_layers(num_layers=2)

    first = image.decode_tile_payload_stream(
        0,
        _empty_sop_packet(0),
        params,
    )
    second = image.decode_tile_payload_stream(
        0,
        _empty_sop_packet(0),
        params,
    )

    assert first.positions == 1
    assert first.body == 7
    assert second.positions == 1
    assert second.body == 7
    assert "packet_sequence" not in image.tiles[0]
