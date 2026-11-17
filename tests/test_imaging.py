import pytest

from vroid_mcp import profiles
from vroid_mcp.vision.imaging import (annotate, find_color_blobs, find_template, region_diff,
                                      resolve_region, similarity, to_model_image)


@pytest.mark.parametrize("spec,box", [
    (None, (0, 0, 1024, 768)),
    ({"bottom": 50}, (0, 0, 1024, 50)),
    ({"left": -310}, (714, 0, 1024, 768)),
    ({"right": 0.5, "top": 0.25}, (0, 192, 512, 768)),
    ({"left": -200, "top": 60, "bottom": 130}, (824, 60, 1024, 130)),
])
def test_resolve_region(spec, box):
    assert resolve_region(spec, 1024, 768) == box


def test_resolve_region_empty_raises():
    with pytest.raises(ValueError):
        resolve_region({"left": 900, "right": 100}, 1024, 768)


def test_model_image_downscale(start_img):
    m = to_model_image(start_img, max_px=512)
    assert m.size == (512, 384) and m.scale == 0.5 and m.mime == "image/jpeg"
    assert len(m.data) < 80_000
    assert to_model_image(start_img, max_px=2000).scale == 1.0


def test_region_diff_and_masks(start_img, dressup_img):
    assert region_diff(start_img, start_img) == 0.0
    assert region_diff(start_img, dressup_img) > 5
    assert region_diff(start_img, dressup_img, region=(900, 0, 1024, 45)) < 1
    assert region_diff(start_img, dressup_img, masks=[(0, 0, 1024, 768)]) == 0.0


def test_similarity(start_img, dressup_img):
    crop = start_img.crop((40, 118, 230, 308))
    assert similarity(crop, crop) > 0.999
    assert similarity(crop, dressup_img.crop((40, 118, 230, 308))) < 0.9


def test_find_template_on_both_screens(start_img, dressup_img):
    tmpl, _ = profiles.load("vroid-2.14.0-en").icon("create_new_plus")
    (score, box), = find_template(start_img, tmpl, max_results=1)
    assert score > 0.95 and abs(box[0] - 118) <= 1 and abs(box[1] - 197) <= 1
    (score, box), = find_template(dressup_img, tmpl, max_results=1)
    assert score > 0.95 and abs(box[1] - (197 + 248)) <= 2  # card moved down 248 px


def test_color_blobs(dressup_img):
    blobs = find_color_blobs(dressup_img, (0, 150, 250), min_area=1500)
    l, t, w, h = blobs[0]  # "Get started with the Dress-up feature" pill
    assert 80 <= l <= 95 and 225 <= t <= 240 and w > 300


def test_annotate_keeps_size(start_img):
    out = annotate(start_img, (0, 0, 100, 100), [((10, 10, 20, 20), "x")], "title")
    assert out.size == start_img.size and out is not start_img
