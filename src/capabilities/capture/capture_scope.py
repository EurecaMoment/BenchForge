"""Optional output selection; scene construction and physics remain unchanged."""


# 验证相机索引、过程帧选择与场景导出开关，返回本次采集覆盖范围。
def capture_scope(program, options=None):
    options = {} if options is None else options
    allowed = {'view_indices', 'export_scene', 'frames', 'every'}
    if not isinstance(options, dict) or set(options) - allowed:
        raise ValueError('capture_options accepts view_indices, export_scene, frames and every')
    count = len(program['cameras'])
    views = options.get('view_indices', list(range(count)))
    if not isinstance(views, list) or any(type(i) is not int or not 0 <= i < count for i in views) or len(set(views)) != len(views):
        raise ValueError('view_indices must contain distinct zero-based camera indices')
    export = options.get('export_scene', False)
    if type(export) is not bool:
        raise ValueError('export_scene must be a boolean')
    frames = options.get('frames', [])
    if not isinstance(frames, list) or any(type(i) is not int or not 1 <= i <= 1200 for i in frames):
        raise ValueError('capture_options.frames must contain step numbers within 1..1200')
    if len(frames) > 1200:
        raise ValueError('capture_options.frames accepts at most 1200 entries')
    every = options.get('every')
    if every is not None and (type(every) is not int or not 1 <= every <= 1200):
        raise ValueError('capture_options.every must be an integer within 1..1200')
    return {'view_indices': views, 'program_camera_count': count,
            'scene_export_requested': export,
            'capture_frames': {'frames': sorted(set(frames)),
                               'every': every},
            'full_scene_capture': len(views) == count and export}
