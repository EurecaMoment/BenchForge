"""Small per-view checks shared by capture adapters and their consumers."""


def assess_view(rgb, depth, objects):
    import numpy as np

    pixels, depth_values = np.asarray(rgb), np.asarray(depth)
    rgb_std = float(pixels.std()) if pixels.size else 0.0
    finite = np.isfinite(depth_values) & (depth_values > 0)
    finite_fraction = float(finite.mean()) if finite.size else 0.0
    count = len(objects)
    reasons = []
    if rgb_std <= 5.0:
        reasons.append('rgb_low_variance')
    if finite_fraction < .05:
        reasons.append('insufficient_finite_depth')
    if count < 1:
        reasons.append('no_visible_entities')
    return {'passed': not reasons, 'rgb_std': rgb_std,
            'finite_depth_fraction': finite_fraction, 'visible_entity_count': count,
            'reasons': reasons, 'thresholds': {'rgb_std_min': 5.0,
                'finite_depth_fraction_min': .05, 'visible_entity_count_min': 1}}
