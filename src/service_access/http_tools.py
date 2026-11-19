"""HTTP clients for the model providers used by production tasks."""

import base64
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import shutil
import time
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.parse import urljoin
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.request import urlopen

import numpy as np
from PIL import Image
from PIL import ImageOps
from pycocotools import mask as coco_mask

from storage.files import write_json
from service_access.request_context import check_cancelled


class ResourceWait(RuntimeError):
    """The configured provider has no capacity for the request yet."""


# 将输入图像编码成模型可接受的数据 URL，不支持的图像格式先转为 RGB PNG。
def _data_url(path):
    path = Path(path)
    mime = mimetypes.guess_type(path)[0]
    if mime in {'image/png', 'image/jpeg', 'image/webp'}:
        return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
    with Image.open(path) as image:
        return _png_data_url(image.convert('RGB'))


# 把内存图像编码为 PNG 数据 URL，供多模态请求内联使用。
def _png_data_url(image):
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


# 按比例扩展检测框并裁剪到图像尺寸内。
def _pad_box(box, size, ratio):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * ratio, (y1 - y0) * ratio
    width, height = size
    return [max(0, x0 - dx), max(0, y0 - dy), min(width, x1 + dx), min(height, y1 + dy)]


# 结合检测置信度、与提示框的交并比及中心包含关系计算候选优先级。
def _box_rank(detection, prompt_box):
    left, top, right, bottom = detection['box_xyxy']
    x0, y0, x1, y1 = prompt_box
    intersection = max(0, min(right, x1) - max(left, x0)) * max(0, min(bottom, y1) - max(top, y0))
    union = (right - left) * (bottom - top) + (x1 - x0) * (y1 - y0) - intersection
    center_inside = left <= (x0 + x1) / 2 <= right and top <= (y0 + y1) / 2 <= bottom
    return detection['score'] + .35 * (intersection / union if union > 0 else 0) + .10 * center_inside


class ModelClient:
    # 绑定提供方端点、模型、超时及请求默认值，并从指定环境变量读取凭据。
    def __init__(self, provider, settings):
        self.provider = provider
        self.url = settings['url'].rstrip('/')
        self.model = settings['model']
        self.timeout = settings.get('timeout_seconds', 180)
        self.defaults = settings.get('request_defaults', {})
        self.headers = {}
        if token_env := settings.get('token_env'):
            token = os.environ.get(token_env)
            if not token:
                raise ValueError(f'providers.{provider}.token_env references unset {token_env}')
            self.headers['Authorization'] = f'Bearer {token}'

    # 发送同源模型请求；拒绝服务返回跨源地址并保留可诊断的错误信息。
    def _open(self, path, method, body=None, *, timeout=None):
        url = urljoin(self.url + '/', path)
        if urlsplit(url).netloc != urlsplit(self.url).netloc or urlsplit(url).scheme != urlsplit(self.url).scheme:
            raise ValueError(f'{self.provider} returned a URL outside its configured origin')
        headers = dict(self.headers)
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body).encode('utf-8')
        try:
            return urlopen(Request(url, data=body, headers=headers, method=method), timeout=timeout or self.timeout)
        except HTTPError as exc:
            with exc:
                try:
                    error = json.load(exc)['error']['message']
                except (ValueError, KeyError, TypeError):
                    error = exc.reason
            message = f'{self.provider} {method} {path}: HTTP {exc.code} {str(error)[:300]}'
            if exc.code in (429, 503):
                raise ResourceWait(message) from None
            raise RuntimeError(message) from None
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise exc.reason from exc
            raise

    # 调用模型接口并解析 JSON 响应，可单独覆盖此次请求超时。
    def _json(self, path, method='GET', body=None, *, timeout=None):
        with self._open(path, method, body, timeout=timeout) as response:
            return json.load(response)

    # 先下载到临时文件，完成后替换目标，避免暴露未完成的模型产物。
    def _download(self, path, output):
        temporary = output.with_suffix(output.suffix + '.part')
        with self._open(path, 'GET') as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        temporary.replace(output)

    # 构造文生图或图像编辑请求，合并尺寸与采样参数并保存生成结果。
    def diffusion(self, request):
        size = self.defaults.get('size', '1024x1024')
        if 'width' in request or 'height' in request:
            width, height = request.get('width', 1024), request.get('height', 1024)
            size = f'{width}x{height}'
        payload = {**self.defaults, 'model': self.model, 'prompt': request['prompt'],
                   'size': size, 'seed': request['seed'],
                   'num_inference_steps': request['steps'], 'n': 1, 'response_format': 'b64_json',
                   'output_format': 'png'}
        route = 'images/generations'
        if request.get('image'):
            payload['image'] = _data_url(request['image'])
            route = 'images/edits'
        result = self._json(route, 'POST', payload)
        output = Path(request['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(result['data'][0]['b64_json']))
        return {'output': str(output)}

    # 按文本及实例提示逐项分割图像，处理中响应取消并汇总掩码产物。
    def sam3(self, request, stop):
        output = Path(request['output_dir'])
        output.mkdir(parents=True, exist_ok=True)
        image_url = _data_url(request['image'])
        with Image.open(request['image']) as image:
            size = image.size
        prompts = [(prompt, None) for prompt in request.get('prompts', [])]
        prompts.extend((item.get('segmentation_prompt') or item['label'], item)
                       for item in request.get('instances', []))
        segments = []
        for prompt, instance in prompts:
            check_cancelled(stop, 'segmentation')
            content = [{'type': 'text', 'text': prompt},
                       {'type': 'image_url', 'image_url': {'url': image_url}}]
            payload = {**self.defaults, 'model': self.model, 'threshold': request['confidence'], 'stream': False,
                       'task': 'text', 'messages': [{'role': 'user', 'content': content}]}
            if instance is not None:
                box = _pad_box(instance['box'], size, request.get('box_padding_ratio', .03))
                payload.update(task='geometry', boxes_xyxy=[box], box_labels=[True])
            completion = self._json('chat/completions', 'POST', payload)
            result = json.loads(completion['choices'][0]['message']['content'])
            detections = result['results'][0]['detections']
            details = {}
            if instance is not None and detections:
                ranked = sorted(detections, key=lambda item: _box_rank(item, box), reverse=True)
                margin = _box_rank(ranked[0], box) - _box_rank(ranked[1], box) if len(ranked) > 1 else 1.
                details = {'instance_id': instance['id'], 'prompt_box': box,
                           'candidate_count': len(ranked), 'selection_margin': margin}
                detections = ranked[:1]
            for detection in detections:
                rle = detection['mask_rle']
                mask = coco_mask.decode({'size': rle['size'], 'counts': rle['counts'].encode('ascii')})
                if not mask.any():
                    continue
                serial = len(segments)
                filename = f'mask_{serial:04d}.png'
                Image.fromarray(mask * 255).save(output / filename)
                segments.append({'id': instance['id'] if instance is not None else serial,
                                 'label': instance['label'] if instance is not None else prompt, 'text_prompt': prompt,
                                 'score': detection['score'], 'box': detection['box_xyxy'], 'mask': filename, **details})
        return {'segments': segments}

    # 发送单视图或带相机标定的深度请求，下载并整理深度与预览产物。
    def depth(self, request):
        payload = {**self.defaults, 'model': self.model, 'task': 'depth', 'process_res': request['input_size'],
                   'return_inline': False, 'include_arrays': False, 'image_url': _data_url(request['image'])}
        if 'intrinsics' in request:
            payload.update(task='multiview', intrinsics=[request['intrinsics']], extrinsics=[request['extrinsics']])
        result = self._json('depth/estimations', 'POST', payload)
        output = Path(request['output_dir'])
        output.mkdir(parents=True, exist_ok=True)
        archive, depth_path, preview = (output / name for name in ('depth.npz', 'depth.npy', 'depth.png'))
        self._download(result['depth_npz_url'], archive)
        with np.load(archive, allow_pickle=False) as arrays:
            depth = arrays['depth_m']
        if depth.ndim == 3:
            depth = depth[0]
        np.save(depth_path, depth, allow_pickle=False)
        low, high = np.percentile(depth, (2, 98))
        normalized = (depth - low) / max(float(high - low), 1e-6)
        Image.fromarray(np.uint8(np.clip(normalized, 0, 1) * 255)).save(preview)
        return {'depth': str(depth_path), 'visualization': str(preview), 'archive': str(archive),
                'depth_type': 'estimated_metric_depth', 'units': result['units'],
                'scale': 'model-predicted; larger values indicate farther surfaces',
                'intrinsics': result['intrinsics'], 'extrinsics': result['extrinsics'],
                'extrinsics_convention': result['extrinsics_convention'],
                'intrinsics_coordinates': result['intrinsics_coordinates'], 'pose_conditioned': result['pose_conditioned']}

    # 逐对象提交或恢复网格重建任务，保存远端回执以支持中断后继续。
    def trellis(self, request, work, stop):
        output = Path(request['output_dir'])
        output.mkdir(parents=True, exist_ok=True)
        assets = []
        for item in request['objects']:
            object_id = item['object_id']
            receipt = work / f'{object_id}.job.json'
            check_cancelled(stop, 'reconstruction')
            filename = f'{object_id}.glb'
            target = output / filename
            saved = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
            job_id = saved.get('id')
            if not job_id and saved.get('state') == 'submission_unknown':
                raise RuntimeError(saved['detail'])
            if not job_id or saved.get('state') in {'failed', 'cancelled'}:
                with Image.open(item['image']) as image, Image.open(item['mask']) as mask:
                    if image.size != mask.size:
                        raise ValueError(f'Image and mask dimensions differ for {object_id}')
                    size = (1024, 1024)
                    reference = ImageOps.pad(image.convert('RGB'), size, color='white')
                    selection = ImageOps.pad(mask.convert('L'), size, method=Image.Resampling.NEAREST, color=0)
                payload = {**self.defaults, 'model': self.model, 'image': _png_data_url(reference),
                           'mask': _png_data_url(selection), 'seed': item['seed'], 'wait': False,
                           'timeout_seconds': self.timeout, 'response_format': 'url'}
                try:
                    job_id = self._json('objects/generations', 'POST', payload)['id']
                except TimeoutError as exc:
                    detail = (f'FastTrellis2 submission timed out for {object_id} before a job ID was received. '
                              'Acceptance is unknown. Reconcile the provider job before resubmitting.')
                    write_json(receipt, {'state': 'submission_unknown', 'detail': detail})
                    raise RuntimeError(detail) from exc
                write_json(receipt, {'id': job_id})
                saved = {}
            if saved.get('state') != 'completed' or not target.is_file():
                self._collect_mesh(job_id, receipt, target, stop)
            assets.append({'object_id': object_id, 'asset': filename, 'model': self.model,
                           'source_frame': 'y_up', 'provider_job_id': job_id})
        return {'assets': assets}

    # 轮询网格生成任务直到完成、失败或取消，下载结果并更新持久回执。
    def _collect_mesh(self, job_id, receipt, target, stop):
        route = f'objects/generations/{quote(job_id, safe="")}'
        # 状态查询可以安全重试，给连续失联单独计时，正常运行的任务不消耗这段预算。
        deadline = time.monotonic() + self.timeout
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'FastTrellis2 status unavailable for job {job_id} after '
                                   f'{self.timeout}s. Its receipt is retained for resuming.')
            try:
                job = self._json(route, timeout=min(120, remaining))
            except TimeoutError:
                time.sleep(min(2, max(0, deadline - time.monotonic())))
                continue
            deadline = time.monotonic() + self.timeout
            state = job['status']
            if state == 'completed':
                file_id = quote(job['data'][0]['file_id'], safe='')
                self._download(f'files/{file_id}/content', target)
                write_json(receipt, {'id': job_id, 'state': state})
                return
            if state in {'failed', 'cancelled'}:
                write_json(receipt, {'id': job_id, 'state': state})
                raise RuntimeError(f'FastTrellis2 job {job_id} {state}: {job.get("error")}')
            time.sleep(2)
        # A service restart can resume this job. Operator cancellation stops it remotely.
        if getattr(stop, 'canceled', True):
            self._json(route, 'DELETE', timeout=10)
        check_cancelled(stop, 'reconstruction')
