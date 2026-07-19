from __future__ import annotations

from pathlib import Path

import pytest

from core_jpeg import decode_jpx_image

FIXTURES = sorted(Path(__file__).parent.joinpath("fixtures", "jpx").glob("gradient_*.j*"))


@pytest.mark.smoke
@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.name)
def test_gradient_fixture_decodes(path: Path) -> None:
    image = decode_jpx_image(path.read_bytes())

    assert image.width == 8
    assert image.height == 8
    assert len(image.components) == 1

    component = image.components[0]
    assert component.width == 8
    assert component.height == 8
    assert component.precision == 8
    assert not component.is_signed
    assert len(component.data) == 64
