from __future__ import annotations

from pathlib import Path

import pytest

from core_jpeg.impl.codecs.jpx import tiles
from core_jpeg.impl.codecs.jpx.codestream import JpxImage
from core_jpeg.impl.codecs.jpx.output import decoded_jpx_native_components

FIXTURE = Path(__file__).parent / "fixtures" / "jpx" / "gradient_multitile.j2k"


def test_parallel_decode_matches_serial_native_and_all_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = FIXTURE.read_bytes()

    monkeypatch.setattr(tiles, "JPX_PARALLEL_TILE_MIN_BYTES", 10**9)
    serial = JpxImage()
    assert serial.parse(data)
    serial_native = decoded_jpx_native_components(serial)
    serial_all = serial.to_raw("all")
    serial_default = serial.to_raw("default")

    monkeypatch.setattr(tiles, "JPX_PARALLEL_TILE_MIN_BYTES", 0)
    monkeypatch.setenv(tiles.JPX_WORKER_ENV, "4")
    parallel = JpxImage()
    assert parallel.parse(data)

    assert [entry is not None for entry in parallel.decoded_tile_data] == [True] * 4
    assert all(bool(tile) for tile in parallel.tiles)
    assert decoded_jpx_native_components(parallel) == serial_native
    assert parallel.to_raw("all") == serial_all
    assert parallel.to_raw("default") == serial_default


def test_install_decoded_tile_component_planes_round_trip() -> None:
    image = JpxImage()
    image.width = 2
    image.height = 1
    image.x_end = 2
    image.y_end = 1
    image.components = 1
    image.tile_width = 1
    image.tile_height = 1
    image.tiles_cols = 2
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
    tiles.initialize_tile_slots(image)
    params = image.coding_params()

    tiles.install_decoded_tile_component_planes(image, 0, [[-128]], params)
    tiles.install_decoded_tile_component_planes(image, 1, [[-112]], params)
    image.decoded_tile_data[0] = (1, bytes([0x00]))
    image.decoded_tile_data[1] = (1, bytes([0x10]))

    native = decoded_jpx_native_components(image)
    assert native[0].samples == (0, 16)
    assert image.to_raw("all") == b"\x00\x10"
    assert image.to_raw("default") == b"\x00\x10"
