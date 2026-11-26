"""Translate verified v2 job artifacts into the existing capture evidence contract."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil

import numpy as np
from PIL import Image

from capabilities.capture.quality import assess_view
from storage.files import write_json


# 兼容字典与 protobuf 消息的字段读取。
def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


# 按句柄、绝对路径后缀或名称唯一定位下载产物，歧义即报错。
def resolve_artifact(reference, files):
    """Resolve one downloaded file without trusting a server absolute path."""
    if not isinstance(reference, str) or not reference:
        raise ValueError('Isaac Sim result omitted an artifact reference')
    if reference.startswith('artifact://'):
        matches = [item for item in files if item['artifact_id'] == reference.removeprefix('artifact://')]
    else:
        normalized = reference.replace('\\', '/').rstrip('/')
        # 相对路径是唯一标识：服务端绝对路径以它结尾时优先精确命中，
        # 避免每帧同名的 render.png/depth_m.npy 被 basename 匹配放大成歧义。
        matches = [item for item in files
                   if normalized.endswith('/' + item['relative_path'].lstrip('/'))]
        if not matches:
            matches = [item for item in files if item['name'] == reference]
        if not matches:
            matches = [item for item in files if normalized.endswith('/' + item['name'])]
    if len(matches) != 1:
        raise ValueError('Isaac Sim result references an artifact absent or ambiguous in its manifest')
    return Path(matches[0]['source_path'])


# 提取标签名，兼容 id -> "name" 与 id -> {"class": "name"} 两种服务端结构。
def _label_name(value):
    if isinstance(value, dict):
        return str(value.get('class') or value.get('label')
                   or value.get('name') or value)
    return value if isinstance(value, str) else str(value)


# 把服务端语义标签的常见结构规范化，兼容旧 classes/instances 与扁平映射。
def _labels(labels):
    if not isinstance(labels, dict):
        return {}, {}, None, None
    classes = labels.get('classes') if isinstance(labels.get('classes'), dict) else {}
    instances = labels.get('instances') if isinstance(labels.get('instances'), dict) else {}
    if not classes:
        source = labels.get('idToLabels') or labels.get('labels')
        if not isinstance(source, dict):
            source = {key: value for key, value in labels.items()
                      if isinstance(key, (str, int)) and not isinstance(value, (dict, list))}
        classes = source
    classes = {str(key): _label_name(value) for key, value in classes.items()}
    normalized = {}
    for key, item in instances.items():
        if isinstance(item, dict):
            normalized[str(key)] = str(item.get('prim_path') or item.get('path')
                                       or item.get('label') or item.get('class') or item)
        else:
            normalized[str(key)] = str(item)
    if not normalized:
        normalized = dict(classes)
    depth = labels.get('depth_convention') or labels.get('depthConvention')
    return classes, normalized, depth, labels.get('backend')


# 用标签名分段匹配对象或刚体标识，避免相近名称互相吞并。
def _label_matches(value, tokens):
    parts = set(re.split(r'[/_.:#\s]+', str(value)))
    return any(token and (token == str(value) or token in parts) for token in tokens)


# 由世界位置、目标和上方向构造列向量约定下的相机世界矩阵。
def _camera_matrix(camera):
    position = np.asarray(camera['position'], dtype=float)
    target = np.asarray(camera['target'], dtype=float)
    up = np.asarray(camera.get('up', [0, 0, 1]), dtype=float)
    backward = position - target
    norm = np.linalg.norm(backward)
    if norm <= 1e-9:
        raise ValueError('Camera position and target must differ')
    backward /= norm
    right = np.cross(up, backward)
    if np.linalg.norm(right) <= 1e-9:
        right = np.cross([0, 1, 0], backward)
    right /= np.linalg.norm(right)
    upward = np.cross(backward, right)
    matrix = np.eye(4)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3] = right, upward, backward, position
    return matrix


# 四元数转列向量约定的世界变换矩阵。
def _quaternion_matrix(values):
    if len(values) < 7:
        raise ValueError('Body pose needs seven values')
    x, y, z, w = (float(value) for value in values[3:7])
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-12:
        x, y, z, w = 0.0, 0.0, 0.0, 1.0
    else:
        x, y, z, w = x / norm, y / norm, z / norm, w / norm
    matrix = np.eye(4)
    matrix[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    matrix[:3, 3] = [float(value) for value in values[:3]]
    return matrix


# 把最终刚体位姿写回声明的实体几何，报告仿真后的中心与包围盒。
def entities_from_job(program, job, plan):
    poses = {_get(body, 'id'): _get(body, 'pose_xyzw') for body in _get(job, 'bodies', [])}
    body_extents = {}
    for body in plan.get('bodies') or []:
        body_id = body.get('id')
        if 'size' in body:
            size = body['size']
            body_extents[body_id] = np.asarray([size['x'], size['y'], size['z']], dtype=float)
        elif 'radius' in body:
            radius = float(body['radius'])
            body_extents[body_id] = np.asarray([2 * radius] * 3, dtype=float)
    entities = {}
    dynamic = {entity['id']: entity['dynamic'] for entity in plan['entities'].values()}
    for identity, entity in plan['entities'].items():
        body_ids = entity.get('body_ids') or [identity]
        parts, lows, highs = [], [], []
        for body_id in body_ids:
            pose = poses.get(body_id)
            if pose is None:
                raise ValueError('Isaac Sim job omitted the final pose for ' + body_id)
            matrix = _quaternion_matrix(pose)
            # 复合体的每个 part 有自己的尺寸；只有单刚体实体才用实体包络。
            size = body_extents.get(body_id)
            if size is None:
                size = np.asarray(entity['size'], dtype=float)
            half = np.abs(matrix[:3, :3]) @ (size / 2)
            low = matrix[:3, 3] - half
            high = matrix[:3, 3] + half
            lows.append(low)
            highs.append(high)
            parts.append({'body_id': body_id, 'world_transform': matrix.tolist(),
                          'current_center': matrix[:3, 3].tolist()})
        low, high = np.min(lows, axis=0), np.max(highs, axis=0)
        root = np.eye(4)
        angle = math.radians(float(entity.get('yaw_deg') or 0))
        root[:3, :3] = [[math.cos(angle), -math.sin(angle), 0],
                        [math.sin(angle), math.cos(angle), 0], [0, 0, 1]]
        root[:3, 3] = np.asarray(entity['center'], dtype=float)
        record = {key: value for key, value in entity.items() if key != 'body_ids'}
        record.update({'body_ids': body_ids,
            'world_transform': root.tolist(),
            'world_transform_convention': 'column vector; translation in last column; local to world meters',
            'world_aabb': {'min': low.tolist(), 'max': high.tolist()},
            'current_center': ((low + high) / 2).tolist(),
            'settled_center': ((low + high) / 2).tolist(),
            'bounds_status': 'declared_geometry_after_simulated_pose',
            'collision': {'paths': body_ids, 'source': 'v2 BodyState final pose'},
            'geometry_parts': {'source': 'v2 BodySpec submission',
                               'coordinate_frame': 'world_meters', 'parts': parts},
            'static_body_semantics': None if dynamic.get(identity) else 'upstream_default'})
        entities[identity] = record
    return entities


# 汇总最终刚体线速度和角速度，以显式阈值报告稳定状态及仿真时刻。
def physics_summary(job, steps, dt, plan=None):
    bodies = _get(job, 'bodies', [])
    speeds = [math.sqrt(sum(float(value) ** 2 for value in list(_get(body, 'velocity', []))[:3]))
              for body in bodies]
    angular = [math.sqrt(sum(float(value) ** 2 for value in list(_get(body, 'velocity', []))[3:6]))
               for body in bodies]
    dynamic = (sum(1 for entity in plan['entities'].values() if entity['dynamic'])
               if plan else len(bodies))
    return {'steps': int(steps), 'dt': dt,
            'stable': all(value <= .03 for value in speeds)
            and all(value <= .1 for value in angular),
            'dynamic_bodies': dynamic, 'max_speed_m_s': max(speeds, default=0),
            'max_angular_speed_rad_s': max(angular, default=0), 'physics_tick': steps,
            'device': _get(job, 'physics_device') or None,
            'simulation_seconds': _get(job, 'simulation_seconds'),
            'setup_seconds': _get(job, 'setup_seconds'),
            'physics_seconds': _get(job, 'physics_seconds'),
            'basis': 'isaacsim_rpc.v1 final BodyState velocities after simulation'}


# 读取 .npy 或单数组 .npz，保留服务端数组的原始数据类型。
def _load_array(path):
    loaded = np.load(path, allow_pickle=False)
    if hasattr(loaded, 'files'):
        if len(loaded.files) != 1:
            raise ValueError('Isaac Sim annotation archive must contain one array')
        return np.asarray(loaded[loaded.files[0]])
    return np.asarray(loaded)


# 逐帧保存 RGB、深度和语义数组，并生成与下游一致的 evidence 帧记录。
def frame_evidence(frame, files, camera_index, camera, program, plan, output, *,
                   canonical, physics_tick, dt, job=None):
    output = Path(output)
    step = int(_get(frame, 'frame', 0))
    frame_id = (f'view_{camera_index}' if canonical
                else f'process_view_{camera_index}_step_{step:06d}')
    prefix = output / frame_id
    image = resolve_artifact(_get(frame, 'image'), files)
    depth_file = resolve_artifact(_get(frame, 'depth_m'), files)
    semantic_file = resolve_artifact(_get(frame, 'semantic_ids'), files)
    labels_file = resolve_artifact(_get(frame, 'semantic_labels'), files)
    labels = json.loads(labels_file.read_text())
    classes, instances, depth_convention, backend = _labels(labels)
    depth = _load_array(depth_file)
    semantic = _load_array(semantic_file)
    if semantic.shape != depth.shape:
        raise ValueError('Isaac Sim depth and semantic arrays disagree')
    np.save(str(prefix) + '_depth.npy', depth, allow_pickle=False)
    np.save(str(prefix) + '_semantic.npy', semantic, allow_pickle=False)
    # The v2 capture annotator exposes semantic ids; each BodySpec id is unique,
    # so the same array is the only faithful instance readout available.
    np.save(str(prefix) + '_instance.npy', semantic, allow_pickle=False)
    shutil.copyfile(image, str(prefix) + '.png')
    height, width = semantic.shape
    with Image.open(image) as opened:
        if opened.size != (width, height):
            raise ValueError('Isaac Sim image and annotation resolution disagree')
        rgb = np.asarray(opened.convert('RGB'))
    objects = []
    for entity in plan['entities'].values():
        tokens = [entity['id'], *(entity.get('body_ids') or [])]
        ids = [int(key) for key, value in instances.items() if _label_matches(value, tokens)]
        mask = np.isin(semantic, ids)
        ys, xs = np.where(mask)
        if len(xs) < 30:
            continue
        values = depth[mask]
        values = values[np.isfinite(values) & (values > 0)]
        objects.append({'object_id': entity['id'], 'label': entity.get('label'),
            'mask': {'area_px': int(len(xs))}, 'confidence': {'value': 1.0},
            'valid_for_question_generation': True,
            'depth_z_median': float(np.median(values)) if len(values) else None,
            'bbox_2d': {'xyxy': [int(xs.min()), int(ys.min()),
                                 int(xs.max() + 1), int(ys.max() + 1)]}})
    matrix = _camera_matrix(camera)
    focal = width / (2 * math.tan(math.radians(plan['render']['fov_degrees']) / 2))
    quality = assess_view(rgb, depth, objects)
    result = {'frame_id': frame_id, 'image': frame_id + '.png',
        'image_size': {'width': width, 'height': height},
        'step_id': step,
        'timestamp_sim': (step * dt if step is not None and dt is not None else None),
        'camera': {'position': matrix[:3, 3].tolist(), 'up': matrix[:3, 1].tolist(),
            'target': (matrix[:3, 3] - matrix[:3, 2]).tolist(),
            'T_world_camera_usd': matrix.T.tolist(), 'matrix_layout': 'usd_row_vectors',
            'convention': 'USD camera -Z forward +Y up',
            'K': [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
            'K_basis': 'configured horizontal field of view',
            'fov_source': 'configured_default',
            'declared_fov_degrees': camera.get('fov_degrees'),
            'projection': 'perspective',
            'depth_convention': depth_convention,
            'render_product_resolution': {'width': width, 'height': height},
            'meters_per_scene_unit': 1.0,
            'camera_path': camera.get('prim_path') or camera.get('id'),
            'pixel_center_offset_px': [.5, .5],
            'pixel_coordinates': 'upper-left pixel boundary is (0,0); pixel centers are (u+0.5,v+0.5)',
            'calibration_source': 'reconstructed_from_program_pose; configured_fov'},
        'objects': objects, 'rgb_std': quality['rgb_std'], 'quality': quality,
        'depth_kind': 'optical_axis_z_m', 'depth_unit': 'meter',
        'semantic_info': {'idToLabels': classes},
        'instance_info': {'idToLabels': instances},
        'native_rtx': bool(_get(job, 'native_rtx', False)),
        'annotation_backend': backend or 'isaacsim_rpc.v1',
        'instance_source': 'semantic_ids',
        'fov_degrees': plan['render']['fov_degrees']}
    write_json(Path(str(prefix) + '.json'), result)
    return result
