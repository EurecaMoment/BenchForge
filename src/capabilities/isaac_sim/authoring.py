"""Compile SceneProgram data into portable USD consumed by A100 asset.import.

No Kit or CUDA code runs in BenchForge. Unsupported renderer features fail before
submission instead of silently changing geometry, materials or requested actions.
"""
from __future__ import annotations

import json
from pathlib import Path

from capabilities.isaac_sim.usd_materials import usd, array, material_usd
from capabilities.isaac_sim.mesh_materials import author_mesh_materials
from capabilities.isaac_sim.scene_requirements import (
    UnsupportedScene, UNSUPPORTED_MATERIAL_FIELDS, validate_capture_requirements,
)
from capabilities.scene_authoring.catalog.native import ASSETS
from capabilities.scene_authoring.catalog.native_reference import native_size_z_up
from capabilities.scene_authoring.geometry.mesh import transform_mesh_geometry, transform_mesh_normals
from capabilities.scene_authoring.geometry.preview import preview_scene_geometry
from capabilities.scene_authoring.validation.program import validate_program






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


def material(program, obj, parent=None):
    named = next((item for item in program.get('materials', []) if item['id'] == obj.get('material_id')), {})
    settings = {**(parent or {}), **named, **obj.get('appearance', {})}
    for key in UNSUPPORTED_MATERIAL_FIELDS:
        if key in settings:
            raise UnsupportedScene(f'A100 SceneProgram material {key} has no faithful mapping; inspect the live catalog')
    return {'base_color': named.get('base_color', obj.get('color', (parent or {}).get('base_color', [.7] * 3))),
            'roughness': settings.get('roughness', .85), 'metallic': settings.get('metallic', 0)}




def physics_lines(obj):
    settings = obj.get('physics', {})
    result = []
    if obj.get('dynamic'):
        result += ['bool physics:rigidBodyEnabled = true', f'float physics:mass = {usd(obj["mass_kg"])}']
    if 'linear_damping' in settings or 'angular_damping' in settings:
        raise UnsupportedScene('Author damping through the native scene.edit protocol')
    return result


def compile_scene(program, assets_root, settings):
    """Return USD bytes plus declared inputs; only later native receipts are evidence."""
    validate_program(program)
    validate_capture_requirements(program, assets_root, settings)
    environment = program.get('render_environment', {})
    spp = environment.get('samples_per_pixel', settings.get('samples_per_pixel', 32))
    meshes = {}
    def load(identity):
        if identity not in meshes:
            meshes[identity] = json.loads((Path(assets_root) / identity / 'mesh.json').read_text())
        return meshes[identity]
    preview = preview_scene_geometry(program, mesh_loader=load)
    placements = {row['object_id']: row for row in preview['objects']}
    sources = settings.get('asset_sources', {})
    imports, entities, materials, nodes = [], {}, [], []
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
            attrs, primitive, vertices = [], {'box': 'Cube', 'sphere': 'Sphere', 'cylinder': 'Cylinder', 'mesh': 'Mesh'}.get(shape), None
            if primitive is None:
                raise UnsupportedScene(f'No A100 SceneProgram geometry mapping for {shape}')
            if shape == 'mesh':
                mesh = load(obj['asset_id'])
                vertices, transform = transform_mesh_geometry(mesh['vertices'], obj['size'], mesh.get('coordinate_frame'), obj.get('mesh_transform'))
                triangles = mesh.get('triangles', mesh.get('faces'))
                if triangles is None:
                    raise ValueError('Registered mesh has no triangles')
                attrs += ['point3f[] points = ' + array(vertices.tolist()),
                          'int[] faceVertexCounts = ' + '[' + ','.join('3' for _ in triangles) + ']',
                          'int[] faceVertexIndices = [' + ','.join(str(i) for face in triangles for i in face) + ']',
                          'uniform token subdivisionScheme = "none"']
                mesh_attrs, mesh_materials = author_mesh_materials(mesh, mat_name, mat)
                attrs += mesh_attrs
                materials.extend(mesh_materials)
                if mesh.get('normals'):
                    normals = transform_mesh_normals(mesh['normals'], transform['T_local_from_source'])
                    interpolation = mesh.get('normals_interpolation', 'vertex')
                    expected = len(vertices) if interpolation == 'vertex' else len(triangles) * 3
                    if interpolation not in ('vertex', 'faceVarying') or len(normals) != expected:
                        raise ValueError('Mesh normals do not match their declared interpolation')
                    attrs.append('normal3f[] normals = ' + array(normals.tolist()) +
                                 ' (interpolation = "' + interpolation + '")')
                dims = [1, 1, 1]
                entity['mesh_transform'] = transform
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
            if shape == 'mesh':
                apis.append('PhysicsMeshCollisionAPI')
                attrs.append('uniform token physics:approximation = "convexDecomposition"' if obj['dynamic'] else 'uniform token physics:approximation = "none"')
            children.append(f'def {primitive} "{name}" (prepend apiSchemas = {json.dumps(apis)}) {{\n' + '\n'.join(attrs) + '\n}')
            entity['geometry_parts'].append({'prim_path': visual_path, 'shape': shape,
                'size': part['size'], 'offset': part.get('offset', [0, 0, 0]),
                'rotation_deg_xyz': part.get('rotation_deg_xyz', [0, 0, 0]),
                **({'vertices': vertices.tolist()} if vertices is not None else {})})
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
    return {'usd': text, 'entities': entities, 'imports': imports, 'samples_per_pixel': spp,
            'cameras': ['/World/Scene/Cameras/View' + str(i) for i in range(len(cameras))]}
