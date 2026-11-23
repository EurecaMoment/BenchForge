"""Read measured geometry from the simulator's immutable flattened USD export."""

import hashlib
from pathlib import Path
import numpy as np


class NativeAssetGeometryError(ValueError):
    """Missing exported native geometry or colliders that scene layout cannot repair."""

    def __init__(self, identity, *, geometry_present, colliders_present):
        self.issues = [{
            "code": "native_asset_geometry_missing",
            "path": "/objects",
            "message": "Isaac Sim export did not provide measured geometry and enabled colliders for this native asset.",
            "object_ids": [identity],
            "basis": "isaac_sim_native_export",
            "severity": "error",
            "blocking": True,
            "repair_owner": "isaac_sim_execution",
            "retry_unchanged": False,
            "evidence": {"measured_geometry_present": geometry_present,
                         "enabled_colliders_present": colliders_present},
        }]
        super().__init__("Native asset requires measured geometry and enabled colliders: " + identity)


# 从原生导出中提取完整实体边界，拒绝用目录尺寸包络或截断数组替代测量。
def exported_entities(source, declared):
    """No catalog envelopes or truncated inspection arrays certify native bounds."""
    from pxr import Usd, UsdGeom, UsdPhysics

    source = Path(source)
    stage = Usd.Stage.Open(str(source))
    if not stage:
        raise ValueError("Native scene export could not be opened")
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    transforms = UsdGeom.XformCache(Usd.TimeCode.Default())
    results = {}
    for identity, entity in declared.items():
        root = stage.GetPrimAtPath(entity["prim_path"])
        if not root:
            raise ValueError("Native export omitted entity: " + identity)
        boxes, colliders = [], []
        for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
            if (
                prim.HasAPI(UsdPhysics.CollisionAPI)
                and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
            ):
                colliders.append(str(prim.GetPath()))
            if not prim.IsA(UsdGeom.Boundable):
                continue
            matrix = np.asarray(transforms.GetLocalToWorldTransform(prim), dtype=float)
            if prim.IsA(UsdGeom.PointBased):
                points = np.asarray(UsdGeom.PointBased(prim).GetPointsAttr().Get(), dtype=float)
            else:
                extent = UsdGeom.Boundable.ComputeExtentFromPlugins(
                    UsdGeom.Boundable(prim), Usd.TimeCode.Default()
                )
                if extent is None or len(extent) != 2:
                    raise ValueError(
                        "Native geometry omitted measured extent: " + str(prim.GetPath())
                    )
                from itertools import product

                points = np.asarray(list(product(*zip(*extent))), dtype=float)
            if (
                points.ndim != 2
                or points.shape[1] != 3
                or not len(points)
                or not np.isfinite(points).all()
            ):
                raise ValueError("Native geometry has no finite points: " + str(prim.GetPath()))
            world = points @ matrix[:3, :3] + matrix[3, :3]
            boxes.append((world.min(axis=0), world.max(axis=0)))
        if not boxes or not colliders:
            raise NativeAssetGeometryError(identity, geometry_present=bool(boxes),
                                           colliders_present=bool(colliders))
        low, high = (
            np.min([row[0] for row in boxes], axis=0),
            np.max([row[1] for row in boxes], axis=0),
        )
        if not np.isfinite([low, high]).all() or np.any(high <= low):
            raise ValueError("Native asset bounds must have finite positive extent: " + identity)
        results[identity] = {
            **entity,
            "world_transform": np.asarray(transforms.GetLocalToWorldTransform(root)).tolist(),
            "world_transform_convention": "USD row-vector matrix; translation in last row; local to world meters",
            "world_aabb": {"min": low.tolist(), "max": high.tolist()},
            "current_center": ((low + high) / 2).tolist(),
            "settled_center": ((low + high) / 2).tolist(),
            "collision": {"paths": colliders, "source": "scene.export after physics.step"},
            "geometry_evidence": {
                "source": "native_flattened_usd",
                "sha256": digest,
                "file": source.name,
                "parts": len(boxes),
            },
        }
    return results
