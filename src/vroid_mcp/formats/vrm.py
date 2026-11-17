"""Read a .vrm file (glTF binary) and summarise what VRoid exported.

Supports VRM 1.0 (``VRMC_vrm`` extension) and VRM 0.x (``VRM`` extension).
No dependencies beyond Pillow (for texture sizes).
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

from PIL import Image

from ..errors import VroidError

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

REQUIRED_BONES_1 = [
    "hips", "spine", "head",
    "leftUpperArm", "leftLowerArm", "leftHand", "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg", "rightLowerLeg", "rightFoot",
]
REQUIRED_BONES_0 = REQUIRED_BONES_1 + ["chest", "neck"]


class VrmError(VroidError):
    pass


def read_glb(data: bytes) -> tuple[dict, bytes]:
    """Split a GLB into (json, bin)."""
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        raise VrmError("not a glTF binary (.glb/.vrm) file")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise VrmError(f"unsupported glTF version {version}")
    if length > len(data):
        raise VrmError("file is truncated")
    off, gltf, binchunk = 12, None, b""
    while off + 8 <= length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8:off + 8 + clen]
        if ctype == CHUNK_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == CHUNK_BIN and not binchunk:
            binchunk = chunk
        off += 8 + clen
    if gltf is None:
        raise VrmError("GLB has no JSON chunk")
    return gltf, binchunk


def _buffer_view_bytes(gltf: dict, binchunk: bytes, index: int) -> bytes:
    bv = gltf["bufferViews"][index]
    start = bv.get("byteOffset", 0)
    return binchunk[start:start + bv["byteLength"]]


def _textures(gltf: dict, binchunk: bytes) -> list[dict]:
    out = []
    for i, im in enumerate(gltf.get("images", [])):
        item = {"index": i, "name": im.get("name"), "mime": im.get("mimeType")}
        if "bufferView" in im:
            raw = _buffer_view_bytes(gltf, binchunk, im["bufferView"])
            item["bytes"] = len(raw)
            try:
                with Image.open(io.BytesIO(raw)) as pic:
                    item["width"], item["height"] = pic.size
            except Exception:
                item["width"] = item["height"] = None
        else:
            item["uri"] = im.get("uri")
        out.append(item)
    return out


def _meta_1(meta: dict) -> dict:
    return {
        "name": meta.get("name"),
        "version": meta.get("version"),
        "authors": meta.get("authors", []),
        "copyright": meta.get("copyrightInformation"),
        "contact": meta.get("contactInformation"),
        "license_url": meta.get("licenseUrl"),
        "avatar_permission": meta.get("avatarPermission"),
        "commercial_usage": meta.get("commercialUsage"),
        "allow_redistribution": meta.get("allowRedistribution"),
        "modification": meta.get("modification"),
        "credit_notation": meta.get("creditNotation"),
        "violent": meta.get("allowExcessivelyViolentUsage"),
        "sexual": meta.get("allowExcessivelySexualUsage"),
        "political_religious": meta.get("allowPoliticalOrReligiousUsage"),
        "antisocial_hate": meta.get("allowAntisocialOrHateUsage"),
    }


def _meta_0(meta: dict) -> dict:
    author = meta.get("author")
    return {
        "name": meta.get("title"),
        "version": meta.get("version"),
        "authors": [a.strip() for a in author.split(",")] if author else [],
        "contact": meta.get("contactInformation"),
        "reference": meta.get("reference"),
        "avatar_permission": meta.get("allowedUserName"),
        "violent": meta.get("violentUssageName"),
        "sexual": meta.get("sexualUssageName"),
        "commercial_usage": meta.get("commercialUssageName"),
        "license": meta.get("licenseName"),
    }


def inspect_bytes(data: bytes) -> dict:
    gltf, binchunk = read_glb(data)
    ext = gltf.get("extensions", {})
    out: dict = {"file_bytes": len(data), "generator": gltf.get("asset", {}).get("generator")}

    if "VRMC_vrm" in ext:
        v = ext["VRMC_vrm"]
        out["vrm_version"] = "1.0"
        out["spec_version"] = v.get("specVersion")
        out["meta"] = _meta_1(v.get("meta", {}))
        bones = sorted(v.get("humanoid", {}).get("humanBones", {}))
        required = REQUIRED_BONES_1
        expr = v.get("expressions", {})
        out["expressions"] = {"preset": sorted(expr.get("preset", {})),
                              "custom": sorted(expr.get("custom", {}))}
        out["spring_bones"] = len(ext.get("VRMC_springBone", {}).get("springs", []))
    elif "VRM" in ext:
        v = ext["VRM"]
        out["vrm_version"] = "0.x"
        out["spec_version"] = v.get("specVersion")
        out["exporter"] = v.get("exporterVersion")
        out["meta"] = _meta_0(v.get("meta", {}))
        bones = sorted(b["bone"] for b in v.get("humanoid", {}).get("humanBones", []) if "bone" in b)
        required = REQUIRED_BONES_0
        groups = v.get("blendShapeMaster", {}).get("blendShapeGroups", [])
        out["expressions"] = {
            "preset": sorted({g.get("presetName") for g in groups
                              if g.get("presetName") not in (None, "", "unknown")}),
            "custom": sorted(g.get("name") for g in groups
                             if g.get("presetName") in (None, "", "unknown")),
        }
        out["spring_bones"] = len(v.get("secondaryAnimation", {}).get("boneGroups", []))
    else:
        raise VrmError("glTF file has no VRM extension (VRMC_vrm or VRM)")

    out["humanoid_bone_count"] = len(bones)
    out["humanoid_bones"] = bones
    out["missing_required_bones"] = [b for b in required if b not in bones]

    meshes = gltf.get("meshes", [])
    morphs = set()
    vertices = 0
    accessors = gltf.get("accessors", [])
    for m in meshes:
        names = (m.get("extras") or {}).get("targetNames") or []
        for p in m.get("primitives", []):
            pos = p.get("attributes", {}).get("POSITION")
            if pos is not None and pos < len(accessors):
                vertices += accessors[pos].get("count", 0)
            if not names:
                names = (p.get("extras") or {}).get("targetNames") or []
        morphs.update(names)
    out["blendshapes"] = sorted(morphs)
    out["counts"] = {
        "nodes": len(gltf.get("nodes", [])), "meshes": len(meshes),
        "materials": len(gltf.get("materials", [])), "skins": len(gltf.get("skins", [])),
        "vertices": vertices, "blendshapes": len(morphs),
        "textures": len(gltf.get("images", [])),
    }
    out["textures"] = _textures(gltf, binchunk)
    return out


def inspect_vrm(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise VrmError(f"{p} does not exist")
    out = inspect_bytes(p.read_bytes())
    out["path"] = str(p.resolve())
    return out


def summary(info: dict, max_list: int = 20) -> dict:
    """Shorter view for tool output (long lists truncated)."""
    def cut(xs):
        return xs if len(xs) <= max_list else xs[:max_list] + [f"... +{len(xs) - max_list} more"]
    s = dict(info)
    s["humanoid_bones"] = cut(info["humanoid_bones"])
    s["blendshapes"] = cut(info["blendshapes"])
    s["textures"] = [{k: t.get(k) for k in ("index", "name", "width", "height", "bytes")}
                     for t in info["textures"]][:max_list]
    return s
