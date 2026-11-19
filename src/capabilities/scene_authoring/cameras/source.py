"""Record predicted source-camera intrinsics without altering depth inference."""
import math
import numpy as np


# 将模型的图像归一化内参转换为带宽高及坐标约定的源相机记录。
def source_camera_metadata(intrinsics, image_size):
    """MoGe K uses image-normalized coordinates; dimensions are width, height."""
    width, height = image_size
    matrix = np.asarray(intrinsics, dtype=np.float64)
    pixels = np.diag([width, height, 1]) @ matrix
    return {
        'intrinsics_normalized': matrix.tolist(),
        'intrinsics_pixels': pixels.tolist(),
        'image_size': [width, height],
        'horizontal_fov_deg': math.degrees(2 * math.atan(0.5 / matrix[0, 0])),
        'vertical_fov_deg': math.degrees(2 * math.atan(0.5 / matrix[1, 1])),
        'camera_coordinates': 'opencv_right_down_forward',
        'source': 'MoGe depth-model output used by SAM3D',
        'authority': 'predicted camera intrinsics; not measured calibration or GT',
        'gt_source': False,
    }


# 包装深度推理函数以记录相机信息，同时原样返回模型输出对象。
def record_source_camera(depth_model, record):
    """Wrap the existing model callable; return its exact output object."""
    # 运行深度模型并更新当前输入图像对应的源相机标定。
    def infer(image):
        result = depth_model(image)
        record.clear()
        if result.get('intrinsics') is not None:
            height, width = image.shape[-2:]
            record.update(source_camera_metadata(result['intrinsics'].detach().float().cpu().tolist(),
                                                 [width, height]))
        return result
    return infer
