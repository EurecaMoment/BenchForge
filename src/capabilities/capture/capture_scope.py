"""Optional output selection; scene construction and physics remain unchanged."""


# 验证零起始且不重复的相机索引和场景导出开关，返回本次采集覆盖范围。
def capture_scope(program, options=None):
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {'view_indices', 'export_scene', 'frames', 'every'}:
        raise ValueError('capture_options accepts view_indices, export_scene, frames and every')
    count = len(program['cameras'])
    views = options.get('view_indices', list(range(count)))
    if not isinstance(views, list) or any(type(i) is not int or not 0 <= i < count for i in views) or len(set(views)) != len(views):
        raise ValueError('view_indices must contain distinct zero-based camera indices')
    export = options.get('export_scene', True)
    if type(export) is not bool:
        raise ValueError('export_scene must be a boolean')
    scope = {'view_indices': views, 'program_camera_count': count,
            'scene_export_requested': export,
            'full_scene_capture': len(views) == count and export}
    if 'frames' in options or 'every' in options:
        frames, every = options.get('frames', []), options.get('every')
        if (not isinstance(frames, list) or len(frames) > 1200
                or any(type(step) is not int or not 1 <= step <= 1200 for step in frames)):
            raise ValueError('capture_options.frames accepts at most 1200 steps in 1..1200')
        if every is not None and (type(every) is not int or not 1 <= every <= 1200):
            raise ValueError('capture_options.every must be a step interval in 1..1200')
        if frames or every:
            if not views:
                raise ValueError('Process frames require a selected camera')
            scope['capture_frames'] = {'frames': sorted(set(frames)), 'every': every}
    return scope
