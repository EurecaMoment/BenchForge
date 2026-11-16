"""USD literals and PreviewSurface materials shared by scene and mesh authoring."""

import json
import math


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


def array(values):
    return "[" + ", ".join(usd(value) for value in values) + "]"


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
