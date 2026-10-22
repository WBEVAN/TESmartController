"""Windows kit window clip: CSS radius to device pixels."""

from tesmart.kit_window import clip_corner_px


def test_clip_corner_matches_css_pixels_at_96_dpi():
    assert clip_corner_px(18, 96) == 18
    assert clip_corner_px(22, 96) == 22
    assert clip_corner_px(30, 96) == 30


def test_clip_corner_scales_with_dpi():
    assert clip_corner_px(22, 144) == 33
    assert clip_corner_px(22, 192) == 44


def test_clip_corner_never_collapses():
    assert clip_corner_px(0.2, 96) == 1
    assert clip_corner_px(22, 1) == 1
