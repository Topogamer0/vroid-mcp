"""VRM parsing on small synthetic GLB files (VRM 1.0 and 0.x)."""

import io
import json
import struct

import pytest
from PIL import Image

from vroid_mcp.formats.vrm import REQUIRED_BONES_1, VrmError, inspect_bytes, inspect_vrm, summary


def _png(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "PNG")
    return buf.getvalue()


def make_glb(extensions: dict, images: list[bytes] = (), meshes=None, accessors=None) -> bytes:
    bin_parts, views, imgs, off = [], [], [], 0
    for i, data in enumerate(images):
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data)})
        imgs.append({"name": f"tex{i}", "mimeType": "image/png", "bufferView": i})
        pad = (4 - len(data) % 4) % 4
        bin_parts.append(data + b"\0" * pad)
        off += len(data) + pad
    binchunk = b"".join(bin_parts)
    gltf = {"asset": {"version": "2.0", "generator": "test"}, "extensions": extensions,
            "extensionsUsed": list(extensions), "bufferViews": views, "images": imgs,
            "buffers": [{"byteLength": len(binchunk)}] if binchunk else [],
            "meshes": meshes or [], "accessors": accessors or [], "nodes": [{}] * 3}
    js = json.dumps(gltf).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js
    if binchunk:
        body += struct.pack("<II", len(binchunk), 0x004E4942) + binchunk
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


VRM1 = {"VRMC_vrm": {
    "specVersion": "1.0",
    "meta": {"name": "Alice", "version": "1", "authors": ["me", "you"],
             "avatarPermission": "onlyAuthor", "commercialUsage": "personalNonProfit"},
    "humanoid": {"humanBones": {b: {"node": 0} for b in REQUIRED_BONES_1 if b != "head"}},
    "expressions": {"preset": {"happy": {}, "aa": {}}, "custom": {"wink2": {}}},
}}

VRM0 = {"VRM": {
    "specVersion": "0.0", "exporterVersion": "VRoidStudio-1.0",
    "meta": {"title": "Bob", "author": "a, b", "allowedUserName": "Everyone"},
    "humanoid": {"humanBones": [{"bone": "hips", "node": 0}, {"bone": "head", "node": 1}]},
    "blendShapeMaster": {"blendShapeGroups": [
        {"name": "Joy", "presetName": "joy"}, {"name": "Custom1", "presetName": "unknown"}]},
}}


def test_vrm1():
    data = make_glb(VRM1, images=[_png(64, 32), _png(16, 16)],
                    meshes=[{"primitives": [{"attributes": {"POSITION": 0}}],
                             "extras": {"targetNames": ["Fcl_A", "Fcl_B"]}}],
                    accessors=[{"count": 100}])
    info = inspect_bytes(data)
    assert info["vrm_version"] == "1.0"
    assert info["meta"]["name"] == "Alice" and info["meta"]["authors"] == ["me", "you"]
    assert info["missing_required_bones"] == ["head"]
    assert info["expressions"] == {"preset": ["aa", "happy"], "custom": ["wink2"]}
    assert [(t["width"], t["height"]) for t in info["textures"]] == [(64, 32), (16, 16)]
    assert info["counts"]["vertices"] == 100 and info["blendshapes"] == ["Fcl_A", "Fcl_B"]


def test_vrm0():
    info = inspect_bytes(make_glb(VRM0))
    assert info["vrm_version"] == "0.x" and info["meta"]["name"] == "Bob"
    assert info["meta"]["authors"] == ["a", "b"]
    assert info["humanoid_bones"] == ["head", "hips"]
    assert "chest" in info["missing_required_bones"]
    assert info["expressions"] == {"preset": ["joy"], "custom": ["Custom1"]}


@pytest.mark.parametrize("data,msg", [
    (b"not a glb at all....", "not a glTF"),
    (make_glb({}), "no VRM extension"),
])
def test_errors(data, msg):
    with pytest.raises(VrmError, match=msg):
        inspect_bytes(data)


def test_inspect_file_and_summary(tmp_path):
    p = tmp_path / "a.vrm"
    p.write_bytes(make_glb(VRM1))
    info = inspect_vrm(p)
    assert info["path"].endswith("a.vrm")
    s = summary(info, max_list=3)
    assert len(s["humanoid_bones"]) == 4 and s["humanoid_bones"][-1].startswith("...")
    with pytest.raises(VrmError):
        inspect_vrm(tmp_path / "missing.vrm")
