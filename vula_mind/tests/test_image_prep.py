"""Tests for vula/commerce/image_prep.py — flattening a photographed receipt into a clean,
flat-scan-style crop using vision-detected corners (2026-08-27). Never trusts the corner input
blindly; any failure/degenerate case must return the ORIGINAL bytes unchanged."""
import io

import pytest
from PIL import Image

from vula.commerce import image_prep


def _make_jpeg(size=(400, 300), color=(200, 200, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ── _valid_corners ────────────────────────────────────────────────────────────────

def test_valid_corners_accepts_well_formed_input():
    corners = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
    assert image_prep._valid_corners(corners) == [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]


@pytest.mark.parametrize("corners", [
    None,
    [],
    [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]],  # only 3 points
    [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [1.5, 0.9]],  # out of [0,1] range
    [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], "not-a-point"],
    "not-a-list",
    [[0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],  # malformed point
])
def test_valid_corners_rejects_malformed_input(corners):
    assert image_prep._valid_corners(corners) is None


# ── flatten_receipt ────────────────────────────────────────────────────────────────

def test_flatten_receipt_returns_original_when_corners_missing():
    data = _make_jpeg()
    out = image_prep.flatten_receipt(data, None)
    assert out == data


def test_flatten_receipt_returns_original_on_malformed_corners():
    data = _make_jpeg()
    out = image_prep.flatten_receipt(data, [[0.1, 0.1], [0.9, 0.1]])
    assert out == data


def test_flatten_receipt_returns_original_on_degenerate_thin_quad():
    # A near-zero-width sliver — not a real receipt shape, must not be "flattened" into garbage.
    data = _make_jpeg()
    corners = [[0.50, 0.1], [0.501, 0.1], [0.501, 0.9], [0.50, 0.9]]
    out = image_prep.flatten_receipt(data, corners)
    assert out == data


def test_flatten_receipt_produces_a_real_warped_image_on_good_corners():
    data = _make_jpeg(size=(800, 600))
    corners = [[0.1, 0.1], [0.9, 0.15], [0.85, 0.9], [0.15, 0.85]]
    out = image_prep.flatten_receipt(data, corners)
    assert out != data
    # Confirm it's still a valid, openable image.
    img = Image.open(io.BytesIO(out))
    assert img.size[0] > 0 and img.size[1] > 0


def test_flatten_receipt_never_raises_on_corrupt_image_bytes():
    out = image_prep.flatten_receipt(b"not a real image", [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    assert out == b"not a real image"


def test_flatten_receipt_is_deterministic_for_dedup_hashing():
    """Same input bytes + same corners must always produce the same output bytes — the caller
    hashes the flattened result for redelivery dedup, so any non-determinism here would break it."""
    data = _make_jpeg(size=(800, 600))
    corners = [[0.1, 0.1], [0.9, 0.15], [0.85, 0.9], [0.15, 0.85]]
    out1 = image_prep.flatten_receipt(data, corners)
    out2 = image_prep.flatten_receipt(data, corners)
    assert out1 == out2
