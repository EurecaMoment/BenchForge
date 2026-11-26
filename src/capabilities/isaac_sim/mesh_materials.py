"""Compile registered mesh materials into the A100 USD material subset."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path, PurePosixPath

from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from capabilities.isaac_sim.usd_materials import mesh_array, material_usd, usd
from storage.gltf import gltf_document


_A100_TEXTURE_SLOTS = {"base_color", "metallic_roughness", "normal"}


# 旧记录不含透明模式时，只在原始 GLB 所有材质均为不透明时恢复该语义。
def _legacy_glb_is_opaque(asset_directory):
    path = Path(asset_directory) / "asset.glb"
    if path.is_symlink() or not path.is_file():
        return False
    materials = gltf_document(path).get("materials", [])
    return all(record.get("alphaMode", "OPAQUE") == "OPAQUE" for record in materials)


# 检查纹理与因子的合成透明度是否确实改变外观，保留 glTF OPAQUE 的 RGB。
def _check_opacity(record, minimum_alpha):
    factor = record.get("base_color_factor", [1, 1, 1, 1])
    if not isinstance(factor, (list, tuple)) or len(factor) != 4 or any(
        type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
        for value in factor
    ):
        raise ValueError("Mesh base color factor must be finite RGBA in 0..1")
    mode = record.get("alpha_mode")
    if mode == "OPAQUE":
        return
    if mode not in (None, "MASK", "BLEND"):
        raise ValueError(f"Unknown glTF alpha mode: {mode}")
    cutoff = record.get("alpha_cutoff", 0.5) if mode == "MASK" else 1.0
    if type(cutoff) not in (int, float) or not math.isfinite(cutoff) or cutoff < 0:
        raise ValueError("glTF alpha cutoff must be a finite nonnegative number")
    if factor[3] * minimum_alpha < cutoff:
        raise UnsupportedScene(
            f"A100 OmniPBR cannot reproduce base-color opacity for {mode or 'legacy'} material."
        )


# 将注册资产里的 PBR 贴图校验后映射到 Isaac Sim 上传包的相对路径。
def prepare_mesh_textures(mesh, asset_directory, asset_identity):
    if mesh.get("material_textures"):
        raise UnsupportedScene(
            "Legacy material_textures data has no per-material texture contract; "
            "re-register the mesh with materials[].textures."
        )

    records = mesh.get("materials") or []
    if not isinstance(records, list):
        raise ValueError("Mesh materials must be a list")
    legacy_opaque = None
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Mesh material entries must be objects")
        emissive = row.get("emissive_factor", [0, 0, 0])
        if (not isinstance(emissive, (list, tuple)) or len(emissive) != 3
                or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                       for value in emissive)):
            raise ValueError("Mesh emissive_factor must contain three finite nonnegative values")
        if any(value > 0 for value in emissive):
            raise UnsupportedScene(
                "The active A100 material adapter does not map emissive_factor; "
                "the source material is preserved and no emissive contribution is silently dropped."
            )
    root = Path(asset_directory).resolve()
    if any(row.get("textures") for row in records):
        vertices = mesh.get("vertices")
        texcoords = mesh.get("texcoords")
        if not isinstance(vertices, list) or not isinstance(texcoords, list) or len(texcoords) != len(vertices):
            raise UnsupportedScene(
                "Textured A100 meshes require one source UV coordinate per vertex; "
                "the adapter will not invent UVs."
            )
        for coordinate in texcoords:
            if (not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in coordinate)):
                raise ValueError("Mesh texcoords must contain two finite values per vertex")
    asset_key = hashlib.sha256(asset_identity.encode("utf-8")).hexdigest()[:16]
    dependencies = []
    prepared_records = []

    for material_index, row in enumerate(records):
        textures = row.get("textures") or {}
        if not isinstance(textures, dict):
            raise ValueError("Mesh material textures must be an object")
        unsupported = sorted(set(textures) - _A100_TEXTURE_SLOTS)
        if unsupported:
            raise UnsupportedScene(
                "The active A100 material adapter does not map glTF texture slots: "
                + ", ".join(unsupported)
                + ". The source asset is preserved and no texture is silently dropped."
            )

        prepared_textures = {}
        minimum_alpha = 1.0
        for slot, reference in sorted(textures.items()):
            if not isinstance(reference, str) or not reference:
                raise ValueError(f"Mesh texture {slot} must use a relative file path")
            relative = PurePosixPath(reference)
            if ("\\" in reference or ":" in reference or relative.is_absolute()
                    or any(part in {"", ".", ".."} for part in reference.split("/"))):
                raise ValueError("Mesh texture paths must stay inside the registered asset")
            candidate = root.joinpath(*relative.parts)
            for parent in (candidate, *candidate.parents):
                if parent == root:
                    break
                if parent.is_symlink():
                    raise ValueError("Mesh texture dependencies cannot use symbolic links")
            source = candidate.resolve(strict=True)
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError("Mesh texture dependency is outside the registered asset")
            suffix = source.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
                raise ValueError(f"Mesh texture {slot} needs a supported image extension")
            from PIL import Image

            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                if image.width < 1 or image.height < 1:
                    raise ValueError(f"Mesh texture {slot} has invalid dimensions")
                if slot == "base_color" and row.get("alpha_mode") != "OPAQUE":
                    if "A" in image.getbands() or "transparency" in image.info:
                        minimum_alpha = image.convert("RGBA").getchannel("A").getextrema()[0] / 255

            bundle_path = f"textures/{asset_key}_{material_index}_{slot}{suffix}"
            prepared_textures[slot] = bundle_path
            dependency = {"relative_path": bundle_path, "source_path": str(source)}
            if slot == "metallic_roughness":
                factor = row.get("metallic_factor", 1.0)
                if (type(factor) not in (int, float) or not math.isfinite(factor)
                        or not 0 <= factor <= 1):
                    raise ValueError("Mesh metallic_factor must be finite and between 0 and 1")
                dependency["metallic_factor"] = float(factor)
            dependencies.append(dependency)

        if "alpha_mode" not in row and row.get("source_material_type") == "PBRMaterial":
            if legacy_opaque is None:
                legacy_opaque = _legacy_glb_is_opaque(root)
            if legacy_opaque:
                row = {**row, "alpha_mode": "OPAQUE"}
        _check_opacity(row, minimum_alpha)
        prepared_records.append({**row, "textures": prepared_textures})

    return {**mesh, "materials": prepared_records}, dependencies


# 把单个注册材质转换为 A100 Mitsuba 渲染器消费的 OmniPBR 网络。
def _omnipbr_material(name, record, factor, roughness, metallic):
    textures = record.get("textures") or {}
    shader_inputs = [
        'uniform token info:implementationSource = "sourceAsset"',
        "asset info:mdl:sourceAsset = @OmniPBR.mdl@",
        'string info:mdl:sourceAsset:subIdentifier = "OmniPBR"',
        "color3f inputs:diffuse_color_constant = " + usd(factor[:3]),
        "float inputs:reflection_roughness_constant = " + usd(roughness),
        "float inputs:metallic_constant = " + usd(
            1.0 if "metallic_roughness" in textures else metallic
        ),
        "token outputs:out",
    ]
    if "base_color" in textures:
        shader_inputs.append(
            "asset inputs:diffuse_texture = @" + textures["base_color"] + "@"
        )
    if "metallic_roughness" in textures:
        shader_inputs.extend([
            "asset inputs:ORM_texture = @" + textures["metallic_roughness"] + "@",
            "bool inputs:enable_ORM_texture = true",
            "float inputs:RoughnessMultiplier = " + usd(roughness),
        ])
    if "normal" in textures:
        shader_inputs.append(
            "asset inputs:normalmap_texture = @" + textures["normal"] + "@"
        )
    shader = '\n'.join(shader_inputs)
    return f'''def Material "{name}" {{
        token outputs:mdl:surface.connect = </World/Materials/{name}/Surface.outputs:out>
        def Shader "Surface" {{
            {shader}
        }}
    }}'''


# 为一个材质分片生成 UV、颜色和材质定义，保留逐角颜色与面的对应关系。
def author_mesh_materials(mesh, name, fallback, packed=None):
    records = mesh.get("materials") or []
    colors = mesh.get("vertex_colors", mesh.get("colors"))
    faces = mesh.get("triangles", mesh.get("faces"))
    indices = mesh.get("material_index", [0] * len(faces))
    if len(records) > 1:
        raise ValueError("Mesh material authoring requires one material per USD mesh prim")
    if not records:
        records = [{
            "base_color_factor": [*([1, 1, 1] if colors else fallback["base_color"]), 1],
            "roughness_factor": fallback["roughness"],
            "metallic_factor": fallback["metallic"],
            "double_sided": True,
        }]
    record = records[0]
    factor = record.get("base_color_factor", [1, 1, 1, 1])
    roughness = record.get("roughness_factor", fallback["roughness"])
    metallic = record.get("metallic_factor", fallback["metallic"])
    for key, value in (("roughness", roughness), ("metallic", metallic)):
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0 <= value <= 1):
            raise ValueError(f"Mesh {key} factor must be finite and between 0 and 1")
    if len(indices) != len(faces) or any(index != 0 for index in indices):
        raise ValueError("Each USD mesh prim must contain faces for exactly one material")
    if colors and len(colors) != len(mesh["vertices"]):
        raise ValueError("Mesh vertex colors must match its vertices")

    textures = record.get("textures") or {}
    if textures:
        texcoords = mesh.get("texcoords") or []
        if len(texcoords) != len(mesh["vertices"]):
            raise ValueError("Textured mesh UV count must match its vertices")
        attrs = [
            "texCoord2f[] primvars:st = " + mesh_array("primvars:st", texcoords, packed)
            + ' (interpolation = "vertex")'
        ]
        materials = [_omnipbr_material(name, record, factor, roughness, metallic)]
    else:
        settings = {
            **fallback,
            "base_color": factor[:3],
            "opacity": 1.0,
            "roughness": roughness,
            "metallic": metallic,
        }
        attrs = []
        materials = [material_usd(name, settings, vertex_color=bool(colors))]

    attrs.append("uniform bool doubleSided = " + str(record.get("double_sided", True)).lower())
    if colors and not textures:
        source_visuals = mesh.get("appearance", {}).get("source_visuals", [])
        baked = record.get(
            "vertex_colors_include_base_factor",
            bool(source_visuals) and source_visuals[0].get("source_encoding") == "texture",
        )
        legacy_factor = (
            "vertex_colors_include_base_factor" not in record
            and baked
            and not textures
        )
        corner_colors = [
            [
                factor[axis]
                if legacy_factor
                else colors[vertex][axis] * (1 if baked else factor[axis])
                for axis in range(3)
            ]
            for face in faces
            for vertex in face
        ]
        attrs.append(
            "color3f[] primvars:displayColor = "
            + mesh_array("primvars:displayColor", corner_colors, packed)
            + ' (interpolation = "faceVarying")'
        )
    return attrs, materials
