"""Compile SceneProgram data into portable USD consumed by A100 asset.import.

No Kit or CUDA code runs in BenchForge. Unsupported renderer features fail before
submission instead of silently changing geometry, materials or requested actions.
"""
from __future__ import annotations

import json
from pathlib import Path

from capabilities.isaac_sim.usd_materials import usd, mesh_array, material_usd
from capabilities.isaac_sim.mesh_materials import author_mesh_materials, prepare_mesh_textures
from capabilities.isaac_sim.scene_requirements import (
    UnsupportedScene, UNSUPPORTED_MATERIAL_FIELDS, validate_capture_requirements,
)
from capabilities.scene_authoring.catalog.native import ASSETS
from capabilities.scene_authoring.catalog.native_reference import native_size_z_up
from capabilities.scene_authoring.geometry.mesh import transform_mesh_geometry, transform_mesh_normals
from capabilities.scene_authoring.geometry.preview import preview_scene_geometry
from capabilities.scene_authoring.validation.program import validate_program






# 根据相机位置、目标和向上方向构建USD行向量约定的世界变换矩阵。
def camera_matrix(camera):
    import numpy as np
    position = np.asarray(camera['position'], dtype=float)
    forward = np.asarray(camera['target'], dtype=float) - position
    forward /= np.linalg.norm(forward)
    up = np.asarray(camera.get('up', [0, 1, 0] if abs(forward[2]) > .999 else [0, 0, 1]), dtype=float)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    matrix = np.eye(4)
    matrix[:3, :3] = np.asarray([right, up, -forward])
    matrix[3, :3] = position
    return matrix.tolist()


# 合并命名材质、父材质和对象外观，拒绝不能忠实映射的材质字段。
def material(program, obj, parent=None):
    named = next((item for item in program.get('materials', []) if item['id'] == obj.get('material_id')), {})
    settings = {**(parent or {}), **named, **obj.get('appearance', {})}
    for key in UNSUPPORTED_MATERIAL_FIELDS:
        if key in settings:
            raise UnsupportedScene(f'A100 SceneProgram material {key} has no faithful mapping; inspect the live catalog')
    return {'base_color': named.get('base_color', obj.get('color', (parent or {}).get('base_color', [.7] * 3))),
            'roughness': settings.get('roughness', .85), 'metallic': settings.get('metallic', 0)}




# 生成刚体启用和质量的USD声明，需原生协议设置的阻尼参数会被拒绝。
def physics_lines(obj):
    settings = obj.get('physics', {})
    result = []
    if obj.get('dynamic'):
        result += ['bool physics:rigidBodyEnabled = true', f'float physics:mass = {usd(obj["mass_kg"])}']
    if 'linear_damping' in settings or 'angular_damping' in settings:
        raise UnsupportedScene('Author damping through the native scene.edit protocol')
    return result


# 每个可见分片只保留其面实际引用的顶点，并以同一索引同步 UV、法线和颜色。
def _mesh_parts(mesh, vertices, transform, name, identity, material_settings, materials, packed_meshes):
    import numpy as np

    faces = np.asarray(mesh.get('triangles', mesh.get('faces')))
    if (faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or faces.dtype.kind not in 'iu' or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError('Registered mesh needs triangles with valid vertex indices')
    records = mesh.get('materials') or [None]
    assignments = mesh.get('material_index', [0] * len(faces))
    if len(assignments) != len(faces) or any(type(i) is not int or not 0 <= i < len(records) for i in assignments):
        raise ValueError('Mesh face material indices must match its triangles')

    normals = None
    interpolation = mesh.get('normals_interpolation', 'vertex')
    if mesh.get('normals'):
        normals = np.asarray(mesh['normals'], dtype=np.float64)
        expected = len(vertices) if interpolation == 'vertex' else len(faces) * 3
        if interpolation not in ('vertex', 'faceVarying') or normals.shape != (expected, 3):
            raise ValueError('Mesh normals do not match their declared interpolation')
        valid = np.isfinite(normals).all(axis=1) & (normals != 0).any(axis=1)
        normals[valid] = transform_mesh_normals(normals[valid], transform['T_local_from_source'])
        if not valid.all():
            # 缺失法线用变换后的三角面法线补齐。零面积的面没有可见表面。
            triangles = vertices[faces]
            geometric = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            lengths = np.linalg.norm(geometric, axis=1)
            keep = lengths > 0
            if not keep.any():
                raise ValueError(f'{identity}: registered mesh has no nondegenerate triangles')
            corners = normals[faces] if interpolation == 'vertex' else normals.reshape(-1, 3, 3)
            valid_corners = valid[faces] if interpolation == 'vertex' else valid.reshape(-1, 3)
            fallback = geometric[keep] / lengths[keep, None]
            normals = np.where(valid_corners[keep, :, None], corners[keep], fallback[:, None, :]).reshape(-1, 3)
            interpolation = 'faceVarying'
            faces = faces[keep]
            assignments = [i for i, include in zip(assignments, keep) if include]

    groups = {}
    for face, index in enumerate(assignments):
        groups.setdefault(index, []).append(face)
    vertex_attributes = {}
    for key in ('texcoords', 'colors', 'vertex_colors'):
        if mesh.get(key):
            if len(mesh[key]) != len(vertices):
                raise ValueError(f'Mesh {key} must match its vertices')
            vertex_attributes[key] = np.asarray(mesh[key])

    separate_collider = len(groups) > 1
    if separate_collider:
        # 整网碰撞保持原拓扑。代理仅供物理使用，不参与渲染与实例标注。
        yield name, vertices, faces, ['uniform token purpose = "proxy"', 'token visibility = "invisible"'], True, None
    visuals = mesh.get('appearance', {}).get('source_visuals', [])
    for index, selected in sorted(groups.items()):
        part_name = f'{name}_Material{index}' if separate_collider else name
        mat_name = f'{identity}_{part_name}'
        if separate_collider:
            used, remap = np.unique(faces[selected], return_inverse=True)
            part_faces = remap.reshape(-1, 3)
            points = vertices[used]
        else:
            used = slice(None)
            part_faces, points = faces, vertices
        subset = {'vertices': points, 'faces': part_faces, 'materials': [records[index]] if records[index] else [],
                  'appearance': {'source_visuals': visuals[index:index + 1]}}
        subset.update({key: values[used].tolist() for key, values in vertex_attributes.items()})
        packed = None if packed_meshes is None else packed_meshes.setdefault(f'/World/Objects/{identity}/{part_name}', {})
        attrs, definitions = author_mesh_materials(subset, mat_name, material_settings, packed)
        materials.extend(definitions)
        attrs.append('rel material:binding = </World/Materials/' + mat_name + '>')
        if normals is not None:
            sliced = (normals[used] if interpolation == 'vertex'
                      else normals.reshape(-1, 3, 3)[selected].reshape(-1, 3))
            attrs.append('normal3f[] normals = ' + mesh_array('normals', sliced, packed)
                         + ' (interpolation = "' + interpolation + '")')
        binding = {'material_index': index, 'material_prim': '/World/Scene/Materials/' + mat_name,
                   'textures': (records[index] or {}).get('textures', {})}
        yield part_name, points, part_faces, attrs, not separate_collider, binding


# 将场景程序编译为USD与显式输入清单，真实执行证据仍由后续原生回执提供。
def compile_scene(program, assets_root, settings, *, packed_meshes=None):
    """Return USD bytes plus declared inputs; only later native receipts are evidence."""
    import numpy as np

    validate_program(program)
    validate_capture_requirements(program, assets_root, settings)
    environment = program.get('render_environment', {})
    spp = max(settings.get('samples_per_pixel', 256), environment.get('samples_per_pixel', 256))
    meshes = {}
    # 按资产标识缓存已登记网格记录，避免编译时重复读取。
    def load(identity):
        if identity not in meshes:
            meshes[identity] = json.loads((Path(assets_root) / identity / 'mesh.json').read_text())
        return meshes[identity]
    preview = preview_scene_geometry(program, mesh_loader=load)
    placements = {row['object_id']: row for row in preview['objects']}
    sources = settings.get('asset_sources', {})
    imports, entities, materials, nodes = [], {}, [], []
    texture_dependencies = {}
    mesh_assets, light_inputs = {}, []
    for obj in program['objects']:
        identity, kind = obj['id'], obj['kind']
        placement = placements[identity]
        if placement.get('unavailable'):
            raise UnsupportedScene(placement['unavailable'])
        center, size = placement['initial_center_m'], placement['actual_size_m']
        root = '/World/Scene/Objects/' + identity
        entity = {'id': identity, 'kind': kind, 'label': obj['label'], 'prim_path': root,
                  'initial_center': center, 'size': size, 'support': obj['support'],
                  'dynamic': obj['dynamic'], 'declared_yaw_deg': obj.get('yaw_deg', 0),
                  'parameter_origin': 'declared synthetic priors'}
        entities[identity] = entity
        if kind in ASSETS or kind == 'native':
            source = obj['native_asset'] if kind == 'native' else sources.get(kind)
            catalog = ({'size': native_size_z_up(source), 'source_up_axis': source['source_up_axis']}
                       if kind == 'native' else ASSETS[kind])
            imports.append({'object': obj, 'path': root, 'source': source,
                            'center': center, 'catalog': catalog})
            continue
        if kind == 'generated':
            raise ValueError('Generate and register the mesh before Isaac Sim capture')
        physics = obj.get('physics', {})
        collider = physics.get('collider')
        if collider and collider != 'convexDecomposition':
            raise UnsupportedScene('Unsupported collision approximation')
        settings_material = material(program, obj)
        parts = obj.get('parts') if kind == 'composite' else None
        if kind == 'container':
            x, y, z = size
            t = min(.008, min(size) / 8)
            parts = [{'shape': 'box', 'size': dims, 'offset': offset, 'color': settings_material['base_color']}
                     for dims, offset in [([x, y, t], [0, 0, -(z-t)/2]),
                         ([t, y, z-t], [-(x-t)/2, 0, t/2]), ([t, y, z-t], [(x-t)/2, 0, t/2]),
                         ([x-2*t, t, z-t], [0, -(y-t)/2, t/2]), ([x-2*t, t, z-t], [0, (y-t)/2, t/2])]]
        parent_apis = ['PhysicsRigidBodyAPI', 'PhysicsMassAPI'] if obj['dynamic'] else []
        header = ' (prepend apiSchemas = ' + json.dumps(parent_apis) + ')' if parent_apis else ''
        children = []
        geometries = parts or [{'shape': kind, 'size': size, 'offset': [0, 0, 0], 'color': settings_material['base_color']}]
        entity['geometry_parts'] = []
        for index, part in enumerate(geometries):
            shape, dims = part['shape'], part['size']
            name = f'Part{index}'
            visual_path = root + '/' + name
            mat_name = identity + '_' + name
            mat = material(program, part, settings_material) if parts else settings_material
            attrs, primitive = [], {'box': 'Cube', 'sphere': 'Sphere', 'cylinder': 'Cylinder', 'mesh': 'Mesh'}.get(shape)
            if primitive is None:
                raise UnsupportedScene(f'No A100 SceneProgram geometry mapping for {shape}')
            if shape == 'mesh':
                mesh, dependencies = prepare_mesh_textures(
                    load(obj['asset_id']), Path(assets_root) / obj['asset_id'], obj['asset_id'])
                for dependency in dependencies:
                    path = dependency['relative_path']
                    if path in texture_dependencies and texture_dependencies[path] != dependency:
                        raise ValueError('A100 texture bundle paths must have unique file content')
                    texture_dependencies[path] = dependency
                vertices, transform = transform_mesh_geometry(mesh['vertices'], obj['size'], mesh.get('coordinate_frame'), obj.get('mesh_transform'))
                mesh_assets[identity] = {'entity_id': identity, 'asset_id': obj['asset_id'],
                    'vertices': len(vertices), 'bindings': [], 'transform': transform}
                for part_name, points, faces, part_attrs, collision, binding in _mesh_parts(
                    mesh, vertices, transform, name, identity, mat, materials, packed_meshes
                ):
                    if binding:
                        mesh_assets[identity]['bindings'].append({'prim_path': root + '/' + part_name, **binding})
                    packed = None if packed_meshes is None else packed_meshes.setdefault(f'/World/Objects/{identity}/{part_name}', {})
                    part_attrs += [
                        'point3f[] points = ' + mesh_array('points', points, packed),
                        'int[] faceVertexCounts = ' + mesh_array('faceVertexCounts', np.full(len(faces), 3, dtype=np.int32), packed),
                        'int[] faceVertexIndices = ' + mesh_array('faceVertexIndices', faces.ravel(), packed),
                        'uniform token subdivisionScheme = "none"',
                        'double3 xformOp:translate = ' + usd(part.get('offset', [0, 0, 0])),
                        'float3 xformOp:rotateXYZ = ' + usd(part.get('rotation_deg_xyz', [0, 0, 0])),
                        'double3 xformOp:scale = (1, 1, 1)',
                        'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]',
                    ]
                    apis = ['MaterialBindingAPI']
                    if collision:
                        apis += ['PhysicsCollisionAPI', 'PhysicsMeshCollisionAPI']
                        part_attrs += [
                            'bool physics:collisionEnabled = true',
                            'rel material:binding:physics = </World/Physics/' + identity + '>',
                            'uniform token physics:approximation = ' + usd('convexDecomposition' if obj['dynamic'] else 'none'),
                        ]
                        entity['geometry_parts'].append({'prim_path': root + '/' + part_name, 'shape': shape,
                            'size': part['size'], 'offset': part.get('offset', [0, 0, 0]),
                            'rotation_deg_xyz': part.get('rotation_deg_xyz', [0, 0, 0]),
                            'vertices': points.tolist()})
                    children.append(f'def Mesh "{part_name}" (prepend apiSchemas = {json.dumps(apis)}) {{\n' +
                                    '\n'.join(part_attrs) + '\n}')
                entity['mesh_transform'] = transform
                continue
            else:
                materials.append(material_usd(mat_name, mat))
                if shape == 'box':
                    attrs.append('double size = 1')
                else:
                    attrs.append('double radius = 0.5')
                    if shape == 'cylinder':
                        attrs += ['double height = 1', 'uniform token axis = "Z"']
                attrs.append('color3f[] primvars:displayColor = [' + usd(mat['base_color']) + ']')
            attrs += ['bool physics:collisionEnabled = true',
                'rel material:binding = </World/Materials/' + mat_name + '>',
                'rel material:binding:physics = </World/Physics/' + identity + '>',
                'double3 xformOp:translate = ' + usd(part.get('offset', [0, 0, 0])),
                'float3 xformOp:rotateXYZ = ' + usd(part.get('rotation_deg_xyz', [0, 0, 0])),
                'double3 xformOp:scale = ' + usd(dims),
                'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"]']
            apis = ['PhysicsCollisionAPI', 'MaterialBindingAPI']
            children.append(f'def {primitive} "{name}" (prepend apiSchemas = {json.dumps(apis)}) {{\n' + '\n'.join(attrs) + '\n}')
            entity['geometry_parts'].append({'prim_path': visual_path, 'shape': shape,
                'size': part['size'], 'offset': part.get('offset', [0, 0, 0]),
                'rotation_deg_xyz': part.get('rotation_deg_xyz', [0, 0, 0])})
        attrs = physics_lines(obj) + ['custom string a100sim:semanticLabel = ' + usd(obj['label']),
            'double3 xformOp:translate = ' + usd(center),
            'float xformOp:rotateZ = ' + usd(obj.get('yaw_deg', 0)),
            'uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateZ"]']
        nodes.append(f'def Xform "{identity}"{header} {{\n' + '\n'.join(attrs + children) + '\n}')
    cameras = []
    for index, camera in enumerate(program['cameras']):
        cameras.append(f'''def Camera "View{index}" {{
            matrix4d xformOp:transform = {usd(camera_matrix(camera))}
            uniform token[] xformOpOrder = ["xformOp:transform"]
            float focalLength = {usd(camera.get('focal_length_mm', 24))}
            float horizontalAperture = {usd(camera.get('horizontal_aperture_mm', 36))}
        }}''')
    lights = []
    for index, light in enumerate(program.get('lights') or [{'kind': 'environment', 'intensity': 1}]):
        if not light.get('enabled', True):
            continue
        kind = 'DomeLight' if light['kind'] == 'environment' else 'RectLight'
        light_inputs.append({'prim_path': '/World/Scene/Lights/Light' + str(index), 'kind': kind,
            'authored': {key: light[key] for key in ('id', 'color', 'intensity', 'size') if key in light}})
        attrs = ['color3f inputs:color = ' + usd(light.get('color', [1, 1, 1])),
                 'float inputs:intensity = ' + usd(light.get('intensity', 1))]
        if kind == 'RectLight':
            position, direction = light['position'], light.get('direction', [0, 0, -1])
            attrs += ['float inputs:width = ' + usd(light.get('size', [1, 1])[0]),
                'float inputs:height = ' + usd(light.get('size', [1, 1])[1]),
                'matrix4d xformOp:transform = ' + usd(camera_matrix({'position': position, 'target': [p+d for p, d in zip(position, direction)]})),
                'uniform token[] xformOpOrder = ["xformOp:transform"]']
        lights.append(f'def {kind} "Light{index}" {{\n' + '\n'.join(attrs) + '\n}')
    physical_materials = []
    for obj in program['objects']:
        physical = obj.get('physics', {})
        physical_materials.append(f'''def Material "{obj['id']}" (prepend apiSchemas = ["PhysicsMaterialAPI"]) {{
            float physics:staticFriction = {usd(physical.get('static_friction', .5))}
            float physics:dynamicFriction = {usd(physical.get('dynamic_friction', .5))}
            float physics:restitution = {usd(physical.get('restitution', 0))}
        }}''')
    text = '#usda 1.0\n(defaultPrim = "World"\n metersPerUnit = 1\n upAxis = "Z")\ndef Xform "World" {\n'
    for name, content in [('Objects', nodes), ('Cameras', cameras), ('Lights', lights),
                          ('Materials', materials), ('Physics', physical_materials)]:
        text += f'def Scope "{name}" {{\n' + '\n'.join(content) + '\n}\n'
    text += '}\n'
    return {'usd': text, 'entities': entities, 'imports': imports,
            'mesh_assets': mesh_assets, 'light_inputs': light_inputs,
            'texture_dependencies': [texture_dependencies[path]
                                     for path in sorted(texture_dependencies)],
            'samples_per_pixel': spp,
            'cameras': ['/World/Scene/Cameras/View' + str(i) for i in range(len(cameras))]}
