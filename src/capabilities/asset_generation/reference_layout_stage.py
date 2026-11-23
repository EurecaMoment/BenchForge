"""Build an editable, image-derived starting layout for scene authoring."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import json
from pathlib import Path
import zlib

import numpy as np
from PIL import Image
from PIL import ImageDraw

from capabilities.scene_authoring.geometry.reference_layout import analyze_reference_layout
from capabilities.scene_authoring.validation.asset_policy import layout_representation
from storage.files import write_json
from service_access.progress import record_progress


_DEFAULT_LAYOUT_CAMERA = {
    'position': [0, -8, 4], 'target': [0, 0, 1.2], 'up': [0, 0, 1],
    'focal_length_mm': 24, 'horizontal_aperture_mm': 36,
}


# 按图像边界框面积和分数排序，并抑制重复检测框。
def _select_detections(segments, prompt, count, excluded=()):
    candidates = []
    for segment in segments:
        if segment.get('text_prompt', segment.get('label')) != prompt:
            continue
        box = segment.get('box')
        if not isinstance(box, list) or len(box) != 4:
            continue
        x0, y0, x1, y1 = box
        if not all(type(value) in (int, float) and np.isfinite(value) for value in box):
            continue
        area = max(0, x1 - x0) * max(0, y1 - y0)
        if area <= 0:
            continue
        score = segment.get('score', 0)
        score = float(score) if type(score) in (int, float) and np.isfinite(score) else 0.0
        candidates.append((score * area, segment))
    candidates.sort(key=lambda row: row[0], reverse=True)

    selected = []
    for _, candidate in candidates:
        x0, y0, x1, y1 = candidate['box']
        duplicate = False
        for prior in (*excluded, *selected):
            px0, py0, px1, py1 = prior['box']
            intersection = max(0, min(x1, px1) - max(x0, px0)) * max(0, min(y1, py1) - max(y0, py0))
            union = ((x1 - x0) * (y1 - y0) + (px1 - px0) * (py1 - py0) - intersection)
            if union and intersection / union > 0.65:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
        if len(selected) == count:
            break
    return selected


# 将深度数组按有效像素权重缩放到原图尺寸，避免 NaN 污染插值结果。
def _align_depth(depth, image_size):
    width, height = image_size
    values = np.asarray(depth, dtype=np.float32)
    if values.ndim == 3 and values.shape[0] == 1:
        values = values[0]
    if values.ndim != 2 or not values.size:
        raise ValueError('FastDA3 output must be a nonempty 2D depth array')
    finite = np.isfinite(values) & (values > 0)
    if not finite.any():
        raise ValueError('FastDA3 output contains no finite positive metric depth')
    if values.shape == (height, width):
        return np.where(finite, values, np.nan), {'source_size': [values.shape[1], values.shape[0]],
                                                  'resampled': False}
    valid = finite.astype(np.float32)
    weighted = np.where(finite, values, 0).astype(np.float32)
    def resize(array):
        return np.asarray(Image.fromarray(array, mode='F').resize(
            (width, height), Image.Resampling.BILINEAR), dtype=np.float32)
    resized_weight = resize(valid)
    resized_values = resize(weighted)
    aligned = np.full((height, width), np.nan, dtype=np.float32)
    usable = resized_weight > 0.5
    aligned[usable] = resized_values[usable] / resized_weight[usable]
    if not np.isfinite(aligned).any():
        raise ValueError('FastDA3 depth could not be aligned to the source image')
    return aligned, {'source_size': [values.shape[1], values.shape[0]], 'resampled': True,
                     'method': 'validity-weighted bilinear resampling'}


# 叠加 SAM3 遮罩与对象框，提供可视检查的布局证据图。
def _write_overlay(image_path, proposals, output):
    with Image.open(image_path) as image:
        base = image.convert('RGBA')
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for row in proposals:
        seed = zlib.crc32(row['object_id'].encode('utf-8'))
        color = (64 + seed % 160, 64 + (seed >> 8) % 160, 64 + (seed >> 16) % 160)
        mask = row['mask'] > 0
        layer = np.zeros((base.height, base.width, 4), dtype=np.uint8)
        layer[mask] = (*color, 80)
        overlay = Image.alpha_composite(overlay, Image.fromarray(layer, mode='RGBA'))
        draw = ImageDraw.Draw(overlay)
        box = [round(value) for value in row['box']]
        draw.rectangle(box, outline=(*color, 255), width=3)
        label = row['object_id'] + ' · ' + row['label']
        draw.text((box[0] + 3, max(0, box[1] - 16)), label, fill=(*color, 255),
                  stroke_width=1, stroke_fill=(0, 0, 0, 220))
    Image.alpha_composite(base, overlay).convert('RGB').save(output, format='PNG')


# 注册单个可见实例的 TRELLIS 网格，并保留当前任务的资产访问范围。
def _reconstruct_instance(tools, image, mask_path, item, asset_work, stop, seed):
    return tools.generate_asset({
        'label': item['label'], 'prompt': item['label'],
        'source_image': str(image), 'source_mask': str(mask_path), 'seed': seed,
    }, asset_work, stop)


# 并行调用 SAM3 和 FastDA3，在同一图像上建立对象遮罩及米制深度证据。
def _run_image_analysis(tools, image, spec, work, stop):
    from capabilities.asset_generation.generation import generation_defaults

    segmentation = work / 'segmentation'
    depth_output = work / 'depth'
    prompts = list(dict.fromkeys(item.get('segmentation_prompt', item['label'])
                                 for item in spec['objects']))
    sam_defaults = generation_defaults('sam3')
    depth_defaults = generation_defaults('depth')
    requests = {
        'sam3': ({'image': str(image), 'output_dir': str(segmentation),
                  'confidence': sam_defaults.get('threshold', 0.2), 'prompts': prompts},
                 work / 'sam3'),
        'depth': ({'image': str(image), 'output_dir': str(depth_output),
                   'input_size': depth_defaults.get('process_res', 1024)},
                  work / 'depth_worker'),
    }
    outputs = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='layout-analysis') as executor:
        pending = {executor.submit(tools.execute, name, request, worker_dir, stop): name
                   for name, (request, worker_dir) in requests.items()}
        for future in as_completed(pending):
            name = pending[future]
            if stop.is_set():
                for other in pending:
                    other.cancel()
                raise RuntimeError('reference layout analysis canceled')
            try:
                outputs[name] = future.result()
            except Exception as exc:
                for other in pending:
                    other.cancel()
                raise RuntimeError(f'{name} failed during reference layout analysis: {exc}') from exc
    return outputs['sam3'], outputs['depth'], segmentation


# 以可复用的生产回执运行参考布局分析，登记网格并输出逐对象摆放建议。
def prepare_reference_layout(tools, image, spec, directory, stop):
    image = Path(image).resolve(strict=True)
    directory = Path(directory).resolve()
    work = directory / 'reference_layout'
    work.mkdir(parents=True, exist_ok=True)
    objects = spec['objects']
    contract = {'image': {'path': str(image), 'bytes': image.stat().st_size,
                          'mtime_ns': image.stat().st_mtime_ns},
                'objects': objects, 'camera': spec.get('camera'), 'seed': spec.get('seed', 42)}
    contract_path = work / 'contract.json'
    if contract_path.is_file() and json.loads(contract_path.read_text(encoding='utf-8')) != contract:
        raise ValueError('reference layout inputs changed; create a new design task')
    write_json(contract_path, contract)
    result_path = work / 'layout_estimate.json'
    if result_path.is_file():
        cached_result = json.loads(result_path.read_text(encoding='utf-8'))
        if cached_result.get('status') == 'ready':
            return cached_result
    if stop.is_set():
        raise RuntimeError('reference layout canceled')

    record_progress(directory, 'reference_layout', phase='segment_and_depth')
    sam_result, depth_result, segmentation_dir = _run_image_analysis(tools, image, spec, work, stop)
    with Image.open(image) as reference:
        image_size = reference.size
    raw_depth = np.load(depth_result['depth'], allow_pickle=False)
    depth, depth_alignment = _align_depth(raw_depth, image_size)
    aligned_depth_path = work / 'depth_aligned.npy'
    np.save(aligned_depth_path, depth, allow_pickle=False)

    detections = sam_result.get('segments', [])
    proposals = []
    missing = []
    used_ids = set()
    assigned_detections = []
    for item in objects:
        prompt = item.get('segmentation_prompt', item['label'])
        selected = _select_detections(detections, prompt, item['count'], assigned_detections)
        assigned_detections.extend(selected)
        if len(selected) < item['count']:
            missing.append({'object_id': item['object_id'], 'expected': item['count'],
                            'detected': len(selected), 'segmentation_prompt': prompt})
        for index, detection in enumerate(selected, 1):
            object_id = item['object_id'] if item['count'] == 1 else f"{item['object_id']}_{index}"
            if object_id in used_ids:
                raise ValueError(f'layout instance identifier collides after count expansion: {object_id}')
            used_ids.add(object_id)
            mask_path = segmentation_dir / detection['mask']
            if mask_path.is_symlink() or not mask_path.is_file():
                raise ValueError(f'SAM3 mask is unavailable for {object_id}')
            with Image.open(mask_path) as source_mask:
                if source_mask.size != image_size:
                    raise ValueError(f'SAM3 mask dimensions differ from source image for {object_id}')
                mask = np.asarray(source_mask.convert('L'))
            representation, reason = layout_representation(item)
            proposals.append({'object_id': object_id, 'group_id': item['object_id'],
                              'label': item['label'], 'segmentation_prompt': prompt,
                              'score': detection.get('score'), 'box': detection['box'],
                              'mask_path': str(mask_path), 'mask': mask,
                              'representation': representation, 'representation_reason': reason,
                              'requires_articulation': item['requires_articulation'],
                              'seed': item.get('seed', spec.get('seed', 42))})

    trellis_rows = [item for item in proposals if item['representation'] == 'trellis']
    record_progress(directory, 'reference_layout', phase='reconstruct_objects',
                    trellis_objects=len(trellis_rows), native_objects=len(proposals) - len(trellis_rows))
    reconstruction_errors = []
    if trellis_rows:
        with ThreadPoolExecutor(max_workers=min(3, len(trellis_rows)),
                                thread_name_prefix='layout-trellis') as executor:
            pending = {
                executor.submit(_reconstruct_instance, tools, image, row['mask_path'],
                                row, work / 'assets' / row['object_id'], stop, row['seed']): row
                for row in trellis_rows
            }
            for future in as_completed(pending):
                row = pending[future]
                try:
                    registration = future.result()
                    row['asset_id'] = registration['asset_id']
                    row['asset_geometry'] = registration.get('geometry', {})
                    row['reconstruction_status'] = 'registered'
                except Exception as exc:
                    row['reconstruction_status'] = 'failed'
                    row['reconstruction_error'] = str(exc)[:500]
                    reconstruction_errors.append({'object_id': row['object_id'],
                                                   'detail': str(exc)[:500]})

    camera = spec.get('camera') or _DEFAULT_LAYOUT_CAMERA
    geometry_inputs = []
    for row in proposals:
        value = {'object_id': row['object_id'], 'label': row['label'], 'mask': row['mask']}
        bounds = row.get('asset_geometry', {}).get('bounds')
        if row.get('asset_id') and bounds:
            value.update(asset_id=row['asset_id'], mesh_bounds=bounds)
        geometry_inputs.append(value)

    if geometry_inputs:
        estimate = analyze_reference_layout(
            depth, geometry_inputs, image_size,
            depth_type=depth_result['depth_type'], units=depth_result['units'], camera=camera)
        estimate_by_id = {row['object_id']: row for row in estimate['objects']}
    else:
        estimate = {'schema': 'benchforge.reference-layout/v1', 'authority':
                    'SAM3 masks and FastDA3 predicted depth; estimates only, not GT',
                    'image_size': list(image_size), 'camera': {'source': 'not projected',
                    'world_pose_available': False}, 'metric_scale': {'available': True,
                    'source': 'FastDA3 estimated metric depth'}, 'objects': [],
                    'limits': ['No expected objects were detected by SAM3.']}
        estimate_by_id = {}

    overlay_path = work / 'layout_estimate_overlay.png'
    _write_overlay(image, proposals, overlay_path)

    for row in proposals:
        estimate_row = estimate_by_id.get(row['object_id'], {})
        row.pop('mask', None)
        row['mask'] = {'source_path': row.pop('mask_path'), 'bbox_xyxy_px': row['box']}
        row['position_estimate'] = {
            key: estimate_row[key] for key in (
                'camera_position_m', 'world_position_m', 'projected_size_m',
                'trellis_fit', 'base_z_if_supported_by_ground_m') if key in estimate_row
        }
        if row.get('asset_id'):
            row['mesh_asset_id'] = row.pop('asset_id')
        row.pop('asset_geometry', None)

    failures = []
    if missing:
        failures.append('SAM3 did not find every requested instance')
    if reconstruction_errors:
        failures.append('TRELLIS reconstruction failed for some detected objects')
    result = {
        **estimate,
        'status': 'partial' if failures else 'ready',
        'gt_source': False,
        'reference_image': str(image),
        'overlay_path': str(overlay_path),
        'depth_evidence': {'source_path': str(depth_result['depth']),
                           'aligned_path': str(aligned_depth_path),
                           'depth_type': depth_result['depth_type'],
                           'units': depth_result['units'], 'alignment': depth_alignment},
        'sam3': {'model': sam_result.get('model'), 'detected_instances': len(detections),
                 'source_path': str(segmentation_dir)},
        'camera_assumption': {'source': 'provided layout.camera' if spec.get('camera') else
                              'default virtual scene camera', 'camera': camera},
        'objects': proposals,
        'missing_objects': missing,
        'reconstruction_errors': reconstruction_errors,
        'next_action': 'Use these coordinates and registered asset IDs as the SceneProgram starting point. Inspect the reference and adjust supports, orientation, collisions, room geometry and functional clearances before preflight and capture.',
        'limitations': [
            'SAM3 masks, FastDA3 metric depth and projected dimensions are predictions, not simulator truth.',
            'A generated reference has no measured camera calibration; default-camera world coordinates are an explicit layout assumption.',
            'Image projections do not identify support contacts, hidden geometry or articulated function.',
            'TRELLIS meshes are reconstructed visual assets with unmeasured physical scale; fit sizes are starting estimates.',
        ],
    }
    write_json(result_path, result)
    return result
