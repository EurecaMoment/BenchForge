"""Preserve untextured mesh colors and per-face PBR factors in portable USD."""

import math
from capabilities.isaac_sim.usd_materials import array, material_usd
from capabilities.isaac_sim.scene_requirements import UnsupportedScene


def author_mesh_materials(mesh, name, fallback):
    records = mesh.get("materials") or []
    colors = mesh.get("vertex_colors", mesh.get("colors"))
    if mesh.get("material_textures") or any(row.get("textures") for row in records):
        raise UnsupportedScene(
            "Textured mesh requires native asset import; flattening its materials is not implicit"
        )
    if not records:
        records = [
            {
                "base_color_factor": [*([1, 1, 1] if colors else fallback["base_color"]), 1],
                "roughness_factor": fallback["roughness"],
                "metallic_factor": fallback["metallic"],
                "double_sided": True,
            }
        ]
    faces = mesh.get("triangles", mesh.get("faces"))
    indices = mesh.get("material_index", [0] * len(faces))
    if len(indices) != len(faces) or any(
        type(i) is not int or not 0 <= i < len(records) for i in indices
    ):
        raise ValueError("Mesh face material indices must match its triangles")
    if colors and len(colors) != len(mesh["vertices"]):
        raise ValueError("Mesh vertex colors must match its vertices")
    factors, materials, attrs = [], [], []
    for index, record in enumerate(records):
        factor = record.get("base_color_factor", [1, 1, 1, 1])
        if len(factor) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in factor
        ):
            raise ValueError("Mesh base color factor must be finite RGBA in 0..1")
        factors.append(factor)
        settings = {
            **fallback,
            "base_color": factor[:3],
            "opacity": factor[3],
            "roughness": record.get("roughness_factor", fallback["roughness"]),
            "metallic": record.get("metallic_factor", fallback["metallic"]),
        }
        for key in ("roughness", "metallic"):
            if (
                type(settings[key]) not in (int, float)
                or not math.isfinite(settings[key])
                or not 0 <= settings[key] <= 1
            ):
                raise ValueError("Mesh material factors must be finite values in 0..1")
        material_name = name if index == 0 else name + "_material_" + str(index)
        materials.append(material_usd(material_name, settings, vertex_color=bool(colors)))
        assigned = [i for i, value in enumerate(indices) if value == index]
        if len(records) > 1 and assigned:
            attrs.append(
                'def GeomSubset "Material' + str(index) + '" {\n'
                'uniform token elementType = "face"\n'
                'uniform token familyName = "materialBind"\n'
                "int[] indices = [" + ",".join(map(str, assigned)) + "]\n"
                "rel material:binding = </World/Materials/" + material_name + ">\n}"
            )
    sided = {record.get("double_sided", True) for record in records}
    if len(sided) != 1:
        raise ValueError("Mixed double-sided materials require native asset import")
    attrs.append("uniform bool doubleSided = " + str(next(iter(sided))).lower())
    if colors:
        # Applying factors to corner colors preserves distinct materials sharing a vertex.
        source_visuals = mesh.get("appearance", {}).get("source_visuals", [])
        baked = [
            record.get(
                "vertex_colors_include_base_factor",
                index < len(source_visuals)
                and source_visuals[index].get("source_encoding") == "texture",
            )
            for index, record in enumerate(records)
        ]
        # Older exports used a gray fallback when an untextured TextureVisuals
        # returned one material color instead of a color for every vertex.
        legacy_factor = [
            "vertex_colors_include_base_factor" not in record
            and baked[index]
            and not record.get("textures")
            for index, record in enumerate(records)
        ]
        corner_colors = [
            [
                factors[index][axis]
                if legacy_factor[index]
                else colors[vertex][axis] * (1 if baked[index] else factors[index][axis])
                for axis in range(3)
            ]
            for face, index in zip(faces, indices)
            for vertex in face
        ]
        attrs.append(
            "color3f[] primvars:displayColor = "
            + array(corner_colors)
            + ' (interpolation = "faceVarying")'
        )
    return attrs, materials
