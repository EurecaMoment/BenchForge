"""Pinhole depth helpers for editable benchmark task code (no GPU dependency)."""
import math


# 依据图像尺寸和视场角建立明确光学约定的针孔相机标定。
def pinhole_camera(width, height, hfov_degrees=90.0):
    focal = width / (2 * math.tan(math.radians(hfov_degrees) / 2))
    return {'width': width, 'height': height, 'hfov_degrees': hfov_degrees,
            'fx': focal, 'fy': focal, 'cx': (width - 1) / 2, 'cy': (height - 1) / 2,
            'pixel_convention': 'integer pixel centers, x right and y down'}


# 利用相机内参将光轴方向深度换算成沿像素射线的距离。
def axial_depth_to_range(depth, x, y, camera):
    """Convert camera-forward Z depth to Euclidean camera range, in source units."""
    return depth * math.sqrt(1 + ((x-camera['cx'])/camera['fx'])**2 + ((y-camera['cy'])/camera['fy'])**2)
