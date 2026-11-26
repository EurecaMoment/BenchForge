"""USD literals and PreviewSurface materials shared by scene and mesh authoring."""

import json
import math


# 把字符串、布尔、向量或有限数字编码为USD文本字面量。
def usd(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (tuple, list)):
        return "(" + ", ".join(usd(item) for item in value) + ")"
    if not math.isfinite(float(value)):
        raise ValueError("USD numbers must be finite")
    return repr(float(value))


# 将一组值编码为USD数组字面量。
def array(values):
    return "[" + ", ".join(usd(value) for value in values) + "]"


def mesh_array(name, values, packed=None):
    """Write large mesh attributes directly to the binary layer when available."""
    if packed is not None:
        packed[name] = values
        return "[]"
    if name in ("faceVertexIndices", "faceVertexCounts"):
        return "[" + ",".join(map(str, values)) + "]"
    return array(values.tolist() if hasattr(values, "tolist") else values)


# 生成标准预览材质USD，可选择读取网格顶点颜色。
def material_usd(name, settings, *, vertex_color=False):
    color = (
        "color3f inputs:diffuseColor.connect = </World/Materials/" + name + "/Color.outputs:result>"
        if vertex_color
        else "color3f inputs:diffuseColor = " + usd(settings["base_color"])
    )
    reader = (
        """def Shader "Color" {
        uniform token info:id = "UsdPrimvarReader_float3"
        string inputs:varname = "displayColor"
        float3 outputs:result
    }"""
        if vertex_color
        else ""
    )
    return f'''def Material "{name}" {{
        token outputs:surface.connect = </World/Materials/{name}/Surface.outputs:surface>
        def Shader "Surface" {{
            uniform token info:id = "UsdPreviewSurface"
            {color}
            float inputs:roughness = {usd(settings["roughness"])}
            float inputs:metallic = {usd(settings["metallic"])}
            float inputs:opacity = {usd(settings.get("opacity", 1))}
            token outputs:surface
        }}
        {reader}
    }}'''
