"""Estimate editable object positions and dimensions from image evidence."""
import math

import numpy as np


_AXES = {'x': 0, 'y': 1, 'z': 2}
_RELATIVE_DEPTH_TYPES = {'relative_inverse_depth', 'relative_depth'}
_METRIC_DEPTH_TYPES = {'estimated_metric_depth', 'metric_depth', 'metric_depth_estimate'}
_CAMERA_FROM_ASSET = np.array([
    [1.0, 0.0, 0.0],
    [0.0, 0.0, -1.0],
    [0.0, 1.0, 0.0],
])


def _finite_array(value, shape, name):
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f'{name} must be a finite {shape} array')
    return array


def _unit(vector, name):
    length = float(np.linalg.norm(vector))
    if not math.isfinite(length) or length < 1e-8:
        raise ValueError(f'{name} must be nonzero')
    return vector / length


def scene_camera_calibration(camera, image_size):
    """Convert a SceneProgram camera into OpenCV pixel intrinsics and pose."""
    width, height = image_size
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError('image_size must be positive [width, height]')
    focal = camera.get('focal_length_mm', 24)
    aperture = camera.get('horizontal_aperture_mm', 36)
    if (type(focal) not in (int, float) or type(aperture) not in (int, float) or
            not math.isfinite(focal) or not math.isfinite(aperture) or focal <= 0 or aperture <= 0):
        raise ValueError('camera needs positive focal_length_mm and horizontal_aperture_mm')
    position = _finite_array(camera.get('position'), (3,), 'camera.position')
    target = _finite_array(camera.get('target'), (3,), 'camera.target')
    up_hint = _finite_array(camera.get('up', [0, 0, 1]), (3,), 'camera.up')
    forward = _unit(target - position, 'camera direction')
    if 'up' not in camera and np.linalg.norm(np.cross(forward, up_hint)) < 1e-8:
        up_hint = np.array([0.0, 1.0, 0.0])
    right = _unit(np.cross(forward, up_hint), 'camera right direction')
    up = _unit(np.cross(right, forward), 'camera up direction')
    rotation = np.column_stack((right, -up, forward))
    camera_to_world = np.eye(4, dtype=np.float64)
    camera_to_world[:3, :3] = rotation
    camera_to_world[:3, 3] = position
    focal_px = width * float(focal) / float(aperture)
    intrinsics = [[focal_px, 0.0, width / 2],
                  [0.0, focal_px, height / 2],
                  [0.0, 0.0, 1.0]]
    return {'intrinsics_px': intrinsics, 'camera_to_world': camera_to_world.tolist(),
            'image_size': [width, height],
            'source': 'SceneProgram camera optics or schema defaults; design-reference projection assumption',
            'camera_coordinates': 'opencv_right_down_forward', 'world_coordinates': 'scene_x_right_y_forward_z_up'}


def _camera_matrix(image_size, intrinsics_px=None, focal_length_px=None, horizontal_fov_deg=None):
    width, height = image_size
    if intrinsics_px is not None:
        matrix = _finite_array(intrinsics_px, (3, 3), 'intrinsics_px')
        if abs(float(np.linalg.det(matrix))) < 1e-10:
            raise ValueError('intrinsics_px must be invertible')
        if matrix[0, 0] <= 0 or matrix[1, 1] <= 0:
            raise ValueError('intrinsics focal lengths must be positive')
        return matrix
    if focal_length_px is not None:
        if type(focal_length_px) not in (int, float) or not math.isfinite(focal_length_px) or focal_length_px <= 0:
            raise ValueError('focal_length_px must be positive and finite')
        focal = float(focal_length_px)
    elif horizontal_fov_deg is not None:
        if (type(horizontal_fov_deg) not in (int, float) or not math.isfinite(horizontal_fov_deg) or
                not 1 < horizontal_fov_deg < 179):
            raise ValueError('horizontal_fov_deg must be between 1 and 179')
        focal = width / (2 * math.tan(math.radians(float(horizontal_fov_deg)) / 2))
    else:
        return None
    return np.array([[focal, 0.0, width / 2], [0.0, focal, height / 2], [0.0, 0.0, 1.0]])


def _camera_transform(value):
    if value is None:
        return None
    transform = _finite_array(value, (4, 4), 'camera_to_world')
    rotation = transform[:3, :3]
    if (not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-7) or
            not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5) or
            not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-5)):
        raise ValueError('camera_to_world must contain a rigid right-handed pose')
    return transform


def _mask_record(mask, image_size, object_id):
    value = np.asarray(mask)
    width, height = image_size
    if value.shape != (height, width):
        raise ValueError(f'{object_id}: mask dimensions must match the source image')
    foreground = np.isfinite(value) & (value > 0)
    ys, xs = np.nonzero(foreground)
    if not len(xs):
        raise ValueError(f'{object_id}: mask is empty')
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    center_x = float(xs.mean())
    center_y = float(ys.mean())
    inner = foreground.copy()
    inset_x = int((x1 - x0) * .15)
    inset_y = int((y1 - y0) * .15)
    if inset_x or inset_y:
        inner[:y0 + inset_y, :] = False
        inner[max(y0, y1 - inset_y):, :] = False
        inner[:, :x0 + inset_x] = False
        inner[:, max(x0, x1 - inset_x):] = False
    return {'mask': foreground, 'bbox': [x0, y0, x1, y1],
            'center': [center_x, center_y], 'center_sample': inner if inner.any() else foreground,
            'visible_pixels': int(len(xs)), 'width_px': x1 - x0, 'height_px': y1 - y0}


def _depth_calibration(records, depth_type, intrinsics, anchors, depth_direction):
    if not anchors:
        return None
    if depth_type not in _RELATIVE_DEPTH_TYPES:
        raise ValueError('scale_anchors are only valid for relative depth maps')
    if intrinsics is None:
        raise ValueError('relative-depth scale_anchors require camera intrinsics')
    if not isinstance(anchors, list) or len(anchors) < 2:
        raise ValueError('at least two known-size scale_anchors are required to calibrate relative depth')
    records_by_id = {row['object_id']: row for row in records}
    inverse_distances = []
    observed_depths = []
    for anchor in anchors:
        if not isinstance(anchor, dict) or set(anchor) - {'object_id', 'axis', 'size_m'}:
            raise ValueError('scale_anchors require object_id, axis and size_m')
        object_id = anchor.get('object_id')
        axis = anchor.get('axis')
        size_m = anchor.get('size_m')
        if object_id not in records_by_id or axis not in {'x', 'y'}:
            raise ValueError('each scale_anchor must reference an object and use axis x or y')
        if type(size_m) not in (int, float) or not math.isfinite(size_m) or size_m <= 0:
            raise ValueError('scale_anchor size_m must be positive and finite')
        row = records_by_id[object_id]
        focal = intrinsics[0, 0] if axis == 'x' else intrinsics[1, 1]
        pixels = row['mask_stats']['width_px'] if axis == 'x' else row['mask_stats']['height_px']
        distance = float(focal) * float(size_m) / pixels
        inverse_distances.append(1 / distance)
        raw = row['depth_raw_median']
        observed_depths.append(raw if depth_direction == 'larger_nearer' else -raw)
    inverse_distances = np.asarray(inverse_distances, dtype=np.float64)
    observed_depths = np.asarray(observed_depths, dtype=np.float64)
    if np.ptp(inverse_distances) < 1e-8:
        raise ValueError('scale_anchors must span at least two different estimated distances')
    design = np.column_stack((inverse_distances, np.ones(len(inverse_distances))))
    slope, offset = np.linalg.lstsq(design, observed_depths, rcond=None)[0]
    if not math.isfinite(float(slope)) or slope <= 0 or not math.isfinite(float(offset)):
        raise ValueError('scale_anchors do not agree with the depth map near/far ordering')
    residual = observed_depths - design @ [slope, offset]
    return {'raw_depth_per_inverse_meter': float(slope), 'raw_depth_offset': float(offset),
            'anchor_count': len(anchors), 'median_anchor_residual': float(np.median(np.abs(residual))),
            'method': 'linear inverse-depth calibration from known-size objects',
            'authority': 'operator-provided size anchors plus predicted monocular depth; estimate, not GT'}


def _euler_xyz(rotation):
    pitch = math.asin(max(-1.0, min(1.0, -float(rotation[2, 0]))))
    if abs(math.cos(pitch)) > 1e-7:
        roll = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    else:
        roll = 0.0
        yaw = math.atan2(-float(rotation[0, 1]), float(rotation[1, 1]))
    return [math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


def _mesh_fit(source_bounds, projected_size, camera_to_world, width_axis='x', height_axis='z',
              vertices=None):
    bounds = _finite_array(source_bounds, (2, 3), 'mesh_bounds')
    source_extent = bounds[1] - bounds[0]
    if np.any(source_extent <= 1e-8):
        raise ValueError('mesh_bounds must have positive extents')
    if width_axis not in _AXES or height_axis not in _AXES or width_axis == height_axis:
        raise ValueError('mesh width_axis and height_axis must be different x/y/z axes')
    width_m = projected_size['width_m']
    height_m = projected_size['height_m']
    axis_scale = {'width': width_m / source_extent[_AXES[width_axis]],
                  'height': height_m / source_extent[_AXES[height_axis]]}
    scale = min(axis_scale.values())
    local_size = source_extent * scale
    camera_from_asset = _CAMERA_FROM_ASSET.copy()
    if width_axis != 'x' or height_axis != 'z':
        raise ValueError('only the current TRELLIS z-up mapping width=x, height=z is supported')
    world_from_local = camera_to_world[:3, :3] @ camera_from_asset
    if vertices is None:
        source_vertices = np.asarray([
            [x, y, z] for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]
        ], dtype=np.float64)
        geometry_source = 'registered mesh bounds; conservative rotation envelope'
    else:
        source_vertices = np.asarray(vertices, dtype=np.float64)
        if (source_vertices.ndim != 2 or source_vertices.shape[1] != 3 or
                not len(source_vertices) or not np.isfinite(source_vertices).all()):
            raise ValueError('mesh_vertices must be a nonempty finite Nx3 array')
        vertex_bounds = np.stack((source_vertices.min(axis=0), source_vertices.max(axis=0)))
        if not np.allclose(vertex_bounds, bounds, rtol=1e-5, atol=1e-6):
            raise ValueError('mesh_vertices bounds do not match mesh_bounds')
        geometry_source = 'registered mesh vertices; exact rotated vertex bounds'
    world_vertices = source_vertices @ world_from_local.T * scale
    world_size = world_vertices.max(axis=0) - world_vertices.min(axis=0)
    euler = _euler_xyz(world_from_local)
    return {'source_bounds': bounds.tolist(), 'source_extent': source_extent.tolist(),
            'geometry_source': geometry_source,
            'source_frame': 'registered z_up mesh; TRELLIS source is normalized during import',
            'width_axis': width_axis, 'height_axis': height_axis,
            'uniform_scale_m_per_source_unit': float(scale),
            'normalized_mesh_size_m': local_size.tolist(),
            'world_aabb_size_m': world_size.tolist(),
            'scene_program_geometry_fields': {
                'xy': None,
                'size': world_size.tolist(),
                'yaw_deg': 0,
                'mesh_transform': {'orientation_deg_xyz': euler, 'scale_mode': 'uniform_fit'},
            },
            'fit_fraction_width_height': [
                float(local_size[0] / width_m), float(local_size[2] / height_m)],
            'limits': ['uniform scale preserves TRELLIS proportions',
                       'depth extent comes from the TRELLIS shape prior, not the reference image',
                       'assign support and base_z from scene structure; image depth gives world center, not support contact']}


def analyze_reference_layout(depth_map, objects, image_size, *, depth_type, units,
                             intrinsics_px=None, focal_length_px=None, horizontal_fov_deg=None,
                             camera_to_world=None, camera=None, scale_anchors=None,
                             depth_direction=None):
    """Return image-space and, when calibrated, camera/world layout suggestions.

    Each object contains object_id, mask and optional label, mesh_bounds or
    mesh bounds supplied by analyze_reference_files. Relative inverse depth
    stays relative unless two or more known-size anchors calibrate it.
    """
    width, height = image_size
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError('image_size must be positive [width, height]')
    depth = np.asarray(depth_map, dtype=np.float64)
    if depth.shape != (height, width):
        raise ValueError('depth_map dimensions must match image_size')
    if depth_type not in _METRIC_DEPTH_TYPES | _RELATIVE_DEPTH_TYPES:
        raise ValueError('unsupported depth_type')
    if depth_type in _METRIC_DEPTH_TYPES and units not in {'meter', 'meters', 'm'}:
        raise ValueError('metric depth requires meter units')
    if depth_direction is None:
        depth_direction = 'larger_nearer' if depth_type == 'relative_inverse_depth' else 'larger_farther'
    if depth_direction not in {'larger_nearer', 'larger_farther'}:
        raise ValueError('depth_direction must be larger_nearer or larger_farther')
    if camera is not None:
        if intrinsics_px is not None or camera_to_world is not None:
            raise ValueError('provide either camera or explicit intrinsics/pose, not both')
        calibration = scene_camera_calibration(camera, image_size)
        intrinsics_px = calibration['intrinsics_px']
        camera_to_world = calibration['camera_to_world']
        camera_source = calibration['source']
    else:
        camera_source = 'explicit calibration' if intrinsics_px is not None or camera_to_world is not None else 'unavailable'
    intrinsics = _camera_matrix(image_size, intrinsics_px, focal_length_px, horizontal_fov_deg)
    transform = _camera_transform(camera_to_world)
    if not isinstance(objects, list) or not objects:
        raise ValueError('objects must be a nonempty list')
    records = []
    identities = set()
    valid_depth = np.isfinite(depth)
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get('object_id'), str) or not item['object_id']:
            raise ValueError('each object needs an object_id')
        object_id = item['object_id']
        if object_id in identities:
            raise ValueError(f'duplicate object_id: {object_id}')
        identities.add(object_id)
        stats = _mask_record(item.get('mask'), image_size, object_id)
        values = depth[stats['center_sample'] & valid_depth]
        if not len(values):
            values = depth[stats['mask'] & valid_depth]
        if not len(values):
            raise ValueError(f'{object_id}: mask contains no finite depth values')
        if depth_type in _METRIC_DEPTH_TYPES:
            values = values[values > 0]
            if not len(values):
                raise ValueError(f'{object_id}: metric depth must be positive')
        raw_median = float(np.median(values))
        records.append({'object_id': object_id, 'label': item.get('label', object_id),
                        'mask_stats': stats, 'depth_raw_median': raw_median,
                        'depth_raw_range': [float(np.quantile(values, .1)), float(np.quantile(values, .9))],
                        'mesh_bounds': item.get('mesh_bounds'), 'mesh_vertices': item.get('mesh_vertices'),
                        'asset_id': item.get('asset_id')})
    calibration = _depth_calibration(records, depth_type, intrinsics,
                                      scale_anchors, depth_direction)
    raw_values = np.asarray([row['depth_raw_median'] for row in records], dtype=np.float64)
    forward_values = raw_values if depth_direction == 'larger_farther' else -raw_values
    forward_lo, forward_hi = float(forward_values.min()), float(forward_values.max())
    result_objects = []
    for row in records:
        stats = row['mask_stats']
        raw = row['depth_raw_median']
        if depth_type in _METRIC_DEPTH_TYPES:
            distance = raw
            metric_source = 'metric_depth_array'
        elif calibration:
            inverse = ((raw if depth_direction == 'larger_nearer' else -raw) -
                       calibration['raw_depth_offset']) / calibration['raw_depth_per_inverse_meter']
            distance = 1 / inverse if inverse > 0 else None
            metric_source = 'known_size_anchors' if distance is not None else None
        else:
            distance = None
            metric_source = None
        rank_value = raw if depth_direction == 'larger_farther' else -raw
        forward_rank = 1.0 if forward_hi == forward_lo else (rank_value - forward_lo) / (forward_hi - forward_lo)
        center = stats['center']
        row_result = {'object_id': row['object_id'], 'label': row['label'],
                      'mask': {'bbox_xyxy_px': stats['bbox'], 'center_px': center,
                               'normalized_center_xy': [center[0] / width, center[1] / height],
                               'visible_pixels': stats['visible_pixels']},
                      'depth': {'type': depth_type, 'units': units, 'direction': depth_direction, 'raw_median': raw,
                                'raw_p10_p90': row['depth_raw_range'],
                                'relative_forward_rank': forward_rank,
                                'distance_m': distance, 'metric_source': metric_source}}
        if row['asset_id']:
            row_result['asset_id'] = row['asset_id']
        projected_size = None
        if distance is not None and intrinsics is not None:
            fx, fy = float(intrinsics[0, 0]), float(intrinsics[1, 1])
            projected_size = {'width_m': stats['width_px'] * distance / fx,
                              'height_m': stats['height_px'] * distance / fy,
                              'depth_m': None,
                              'method': 'fronto-parallel pinhole projection of SAM3 mask bounds'}
            camera_xyz = np.linalg.inv(intrinsics) @ [center[0], center[1], 1.0]
            camera_xyz = (camera_xyz / camera_xyz[2] * distance).tolist()
            row_result['camera_position_m'] = camera_xyz
            row_result['projected_size_m'] = projected_size
            if transform is not None:
                world_xyz = (transform[:3, :3] @ np.asarray(camera_xyz) + transform[:3, 3]).tolist()
                row_result['world_position_m'] = world_xyz
        if intrinsics is None:
            row_result['layout_prior'] = {'normalized_center_xy': [center[0] / width, center[1] / height],
                                          'relative_forward_rank': forward_rank,
                                          'world_position': 'unavailable_without_camera_intrinsics_and_metric_scale'}
        elif distance is None:
            row_result['layout_prior'] = {'camera_ray': (np.linalg.inv(intrinsics) @ [center[0], center[1], 1.0]).tolist(),
                                          'relative_forward_rank': forward_rank,
                                          'world_position': 'relative_only; depth has no metric calibration'}
        bounds = row['mesh_bounds']
        if bounds is not None and projected_size is not None and transform is not None:
            fit = _mesh_fit(bounds, projected_size, transform, vertices=row['mesh_vertices'])
            fit['scene_program_geometry_fields']['xy'] = row_result['world_position_m'][:2]
            row_result['trellis_fit'] = fit
            row_result['base_z_if_supported_by_ground_m'] = (
                row_result['world_position_m'][2] - fit['world_aabb_size_m'][2] / 2)
        result_objects.append(row_result)
    metric = depth_type in _METRIC_DEPTH_TYPES or calibration is not None
    return {'schema': 'benchforge.reference-layout/v1',
            'authority': 'SAM3 masks and predicted depth projected through declared camera calibration; design estimate, not GT',
            'image_size': [width, height],
            'camera': {'coordinates': 'opencv_right_down_forward', 'source': camera_source,
                       'intrinsics_px': intrinsics.tolist() if intrinsics is not None else None,
                       'world_pose_available': transform is not None},
            'metric_scale': {'available': metric,
                             'source': 'metric_depth_array' if depth_type in _METRIC_DEPTH_TYPES else
                                       'known_size_anchors' if calibration else 'uncalibrated_relative_depth',
                             'calibration': calibration},
            'objects': result_objects,
            'limits': [
                'SAM3 bounding masks and monocular depth can be wrong or incomplete; inspect their overlays.',
                'Diffusion images have no physical camera calibration; a SceneProgram camera is an explicit projection assumption.',
                'Projection estimates apparent width and height only; object orientation, hidden depth, support and contact need separate evidence.',
                'TRELLIS source bounds supply its shape proportions, not real-world scale.',
                'SceneProgram geometry fields omit support and base_z because image depth alone does not establish support contact.',
            ]}
