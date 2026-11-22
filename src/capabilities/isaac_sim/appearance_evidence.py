"""Record which authored assets, uploaded textures and lights reached native capture."""


# 提取仿真器声明的实际渲染源，避免把计划中的图元当成渲染回执。
def observe_render_sources(by_path, frame):
    for row in frame.get('usd_sources', []):
        if isinstance(row, dict) and isinstance(row.get('prim'), str):
            by_path[row['prim']] = {key: row[key] for key in ('kind', 'radiance') if key in row}


# 对照已校验的不可变上传清单、原生检查和采集回执，记录外观凭据。
def appearance_evidence(compiled, bundle, prims, rendered):
    inspected = {prim['path']: prim.get('type') for prim in prims}
    manifest = {row['relative_path']: row for row in bundle.get('files', [])}
    assets, materials, lights = [], [], []
    for asset in compiled.get('mesh_assets', {}).values():
        transform = asset['transform']
        slots = set()
        for binding in asset['bindings']:
            path = binding['prim_path']
            textures = []
            for slot, relative in sorted(binding['textures'].items()):
                uploaded = manifest.get(relative)
                if not uploaded:
                    raise ValueError(f'Material texture absent from frozen scene upload: {relative}')
                textures.append({'slot': slot, **uploaded})
                slots.add(slot)
            materials.append({'entity_id': asset['entity_id'], 'asset_id': asset['asset_id'],
                'prim_path': path, 'material_prim': binding['material_prim'],
                'material_index': binding['material_index'], 'textures': textures,
                'mesh_inspected': inspected.get(path) == 'Mesh',
                'material_inspected': inspected.get(binding['material_prim']) == 'Material',
                'renderer_reported_mesh': rendered.get(path, {}).get('kind') == 'mesh',
                'appearance_scope': 'USD-authored binding and verified upload; visual appearance requires RGB review'})
        assets.append({'entity_id': asset['entity_id'], 'asset_id': asset['asset_id'],
            'kind': 'mesh', 'render_representation': 'mesh', 'texture_slots': sorted(slots),
            'embedded_geometry': True, 'vertices': asset['vertices'], **transform,
            'uploaded_scene': {key: bundle[key] for key in ('asset_id', 'relative_path', 'sha256')}})
    for light in compiled.get('light_inputs', []):
        path = light['prim_path']
        source = rendered.get(path, {})
        confirmed = source.get('kind') == light['kind']
        lights.append({**light, 'prim_inspected': inspected.get(path) == light['kind'],
                       'renderer_reported': confirmed,
                       **({'renderer_radiance': source['radiance']} if confirmed and 'radiance' in source else {})})
    return {'asset_dependencies': assets, 'material_dependencies': materials, 'light_receipts': lights}
