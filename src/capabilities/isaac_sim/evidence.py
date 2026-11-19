"""Translate native A100 receipts into the existing capture evidence contract."""
from itertools import product
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from storage.files import write_json
from capabilities.capture.quality import assess_view


# 仅用当前任务拥有的产物句柄从下载文件中选择证据。
def resolve_artifact(reference, files):
    """Only job-owned artifact handles can select a downloaded file."""
    if not isinstance(reference, str) or not reference.startswith('artifact://'):
        raise ValueError('Native result did not supply a job artifact handle')
    matches = [item for item in files if item['artifact_id'] == reference.removeprefix('artifact://')]
    if len(matches) != 1:
        raise ValueError('Native result references an artifact absent from its manifest')
    return Path(matches[0]['source_path'])


# 根据原生仿真后的变换和碰撞体提取实体事实，保持上传几何记录不可变。
def measured_entities(compiled, prims):
    """Use native post-step transforms/colliders; uploaded geometry stays immutable."""
    by_path = {row['path']: row for row in prims}
    entities = {}
    for identity, declared in compiled['entities'].items():
        root = declared['prim_path']
        source = by_path.get(root, {})
        transform = source.get('world_matrix')
        if transform is None:
            raise ValueError('Native inspection omitted entity transform: ' + root)
        parts, boxes, colliders = [], [], []
        for part in declared.get('geometry_parts', []):
            prim = by_path.get(part['prim_path'], {})
            matrix = np.asarray(prim.get('world_matrix'), dtype=float)
            if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
                raise ValueError('Native part transform is absent: ' + part['prim_path'])
            attrs = prim.get('attributes', {})
            if 'PhysicsCollisionAPI' not in prim.get('apis', []) or attrs.get('physics:collisionEnabled') not in ('True', 'true', '1'):
                raise ValueError('Native collider is absent: ' + part['prim_path'])
            colliders.append(part['prim_path'])
            if part['shape'] == 'mesh':
                points = np.asarray(part['vertices'], dtype=float)
            else:
                # The native inspector supplies actual unscaled primitive sizes.
                if part['shape'] == 'box':
                    extent = [float(attrs.get('size', 2))] * 3
                elif part['shape'] == 'sphere':
                    extent = [2 * float(attrs.get('radius', 1))] * 3
                else:
                    extent = [2 * float(attrs.get('radius', 1))] * 2 + [float(attrs.get('height', 2))]
                points = np.asarray(list(product(*[(-value / 2, value / 2) for value in extent])))
            world = points @ matrix[:3, :3] + matrix[3, :3]
            if part['shape'] in ('sphere', 'cylinder'):
                axes = matrix[:3, :3] * (np.asarray(extent) / 2)[:, None]
                radius = (np.linalg.norm(axes, axis=0) if part['shape'] == 'sphere'
                          else np.linalg.norm(axes[:2], axis=0) + np.abs(axes[2]))
                boxes.append((matrix[3, :3] - radius, matrix[3, :3] + radius))
            else:
                boxes.append((world.min(axis=0), world.max(axis=0)))
            parts.append({key: value for key, value in part.items() if key != 'vertices'})
        if not boxes:
            # Imported native assets have their own topology. A catalog envelope
            # cannot certify their measured extent: keep that uncertainty explicit.
            entities[identity] = {**{k: v for k, v in declared.items() if k != 'geometry_parts'},
                'world_transform': transform, 'bounds_status': 'requires_native_asset_bounds',
                'collision': {'paths': [row['path'] for row in prims if row['path'].startswith(root + '/')
                    and 'PhysicsCollisionAPI' in row.get('apis', [])]}}
            continue
        low = np.min([item[0] for item in boxes], axis=0)
        high = np.max([item[1] for item in boxes], axis=0)
        entities[identity] = {**{k: v for k, v in declared.items() if k not in ('geometry_parts', 'mesh_transform')},
            'world_transform': transform, 'world_transform_convention': 'USD row-vector matrix; translation in last row; local to world meters',
            'world_aabb': {'min': low.tolist(), 'max': high.tolist()},
            'current_center': ((low + high) / 2).tolist(), 'settled_center': ((low + high) / 2).tolist(),
            'collision': {'paths': colliders, 'source': 'scene.inspect after physics.step'},
            'geometry_parts': {'source': 'executed_primitive_construction',
                'coordinate_frame': 'entity_local_meters', 'parts': parts}}
    return entities


# 汇总原生刚体线速度和角速度，以显式阈值报告稳定状态及仿真时刻。
def physics_summary(observation, steps, dt):
    bodies = observation.get('bodies', [])
    speeds = [float(np.linalg.norm(body['linear_velocity_world_m_s'])) for body in bodies]
    angular = [float(np.linalg.norm(body['angular_velocity_world_rad_s'])) for body in bodies]
    return {'steps': int(steps), 'dt': dt, 'stable': all(value <= .03 for value in speeds)
            and all(value <= .1 for value in angular), 'dynamic_bodies': len(bodies),
            'max_speed_m_s': max(speeds, default=0), 'max_angular_speed_rad_s': max(angular, default=0),
            'physics_tick': observation.get('physics_tick'), 'device': observation.get('device'),
            'basis': 'native CUDA tensors after settling; static geometry still needs bounds/support acceptance'}


# 保存原生RGB及标注数组，结合相机和对象记录形成单帧采集证据。
def save_frame(frame, files, index, program, output, *, physics_tick=None, dt=None):
    prefix = output / f'view_{index}'
    image = resolve_artifact(frame['image'], files)
    annotations = frame.get('annotations', {})
    arrays = resolve_artifact(annotations.get('arrays'), files)
    labels_file = resolve_artifact(annotations.get('labels'), files)
    labels = json.loads(labels_file.read_text())
    with np.load(arrays, allow_pickle=False) as data:
        for source, suffix in [('depth_m', 'depth'), ('semantic_id', 'semantic'), ('instance_id', 'instance'),
                               ('normal_world', 'normal_world'), ('range_m', 'range'), ('valid', 'valid')]:
            np.save(str(prefix) + '_' + suffix + '.npy', data[source], allow_pickle=False)
        instances = data['instance_id'].copy()
        depth = data['depth_m'].copy()
    shutil.copyfile(image, prefix.with_suffix('.png'))
    width, height = map(int, frame['resolution'])
    with Image.open(image) as opened:
        if opened.size != (width, height) or instances.shape != (height, width) or depth.shape != instances.shape:
            raise ValueError('Native image, annotations and camera readout disagree')
        rgb = np.asarray(opened.convert('RGB'))
    objects = []
    for obj in program['objects']:
        root = '/World/Scene/Objects/' + obj['id']
        ids = [int(key) for key, item in labels['instances'].items()
               if item['prim_path'] == root or item['prim_path'].startswith(root + '/')]
        mask = np.isin(instances, ids)
        ys, xs = np.where(mask)
        if len(xs) < 30:
            continue
        values = depth[mask]
        values = values[np.isfinite(values) & (values > 0)]
        objects.append({'object_id': obj['id'], 'label': obj['label'],
            'mask': {'area_px': int(len(xs))},
            'confidence': {'value': 1.0}, 'valid_for_question_generation': True,
            'depth_z_median': float(np.median(values)) if len(values) else None,
            'bbox_2d': {'xyxy': [int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)]}})
    matrix = np.asarray(frame['camera_world'], dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError('Native camera matrix is absent')
    focal = width / (2 * np.tan(np.radians(frame['fov_degrees']) / 2))
    camera = {'position': matrix[:3, 3].tolist(), 'up': matrix[:3, 1].tolist(),
        'target': (matrix[:3, 3] - matrix[:3, 2]).tolist(),
        'T_world_camera_usd': matrix.T.tolist(), 'matrix_layout': 'usd_row_vectors',
        'convention': 'USD camera -Z forward +Y up',
        'K': [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
        'K_basis': 'native rendered horizontal field of view and readout',
        'projection': 'perspective', 'depth_convention': labels['depth_convention'],
        'render_product_resolution': {'width': width, 'height': height},
        'meters_per_scene_unit': 1.0, 'camera_path': frame['camera'],
        'pixel_center_offset_px': [.5, .5],
        'pixel_coordinates': 'upper-left pixel boundary is (0,0); pixel centers are (u+0.5,v+0.5)',
        'calibration_source': 'same A100 CUDA rendered camera matrix, field of view and readout'}
    quality = assess_view(rgb, depth, objects)
    result = {'frame_id': f'view_{index}', 'image': f'view_{index}.png',
        'image_size': {'width': width, 'height': height},
        'step_id': physics_tick, 'timestamp_sim': physics_tick * dt if physics_tick is not None and dt is not None else None,
        'camera': camera, 'objects': objects, 'rgb_std': quality['rgb_std'], 'quality': quality,
        'depth_kind': 'optical_axis_z_m', 'depth_unit': 'meter',
        'semantic_info': {'idToLabels': labels['classes']},
        'instance_info': {'idToLabels': {key: item['prim_path'] for key, item in labels['instances'].items()}},
        'native_rtx': False, 'annotation_backend': labels['backend']}
    write_json(prefix.with_suffix('.json'), result)
    return result
