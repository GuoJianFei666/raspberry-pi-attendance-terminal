#!/usr/bin/env python3
import cv2
import time
import os
import argparse
import numpy as np

try:
    from picamera2 import Picamera2
    from libcamera import controls
    CAMERA_AVAILABLE = True
    CAMERA_IMPORT_ERROR = None
except ImportError as e:
    Picamera2 = None
    controls = None
    CAMERA_AVAILABLE = False
    CAMERA_IMPORT_ERROR = e

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTO_DIR = os.path.join(BASE_DIR, "photos")
MOVIE_DIR = os.path.join(BASE_DIR, "movies")
TEST_DIR = os.path.join(BASE_DIR, "test_results")
MODEL_DIR = os.path.join(BASE_DIR, "downloaded_model")
MODEL_PARAM = os.path.join(MODEL_DIR, "scrfd_500m_bnkps_shape640x640.opt.param")
MODEL_BIN = os.path.join(MODEL_DIR, "scrfd_500m_bnkps_shape640x640.opt.bin")

# ---------------------- 尝试加载 NCNN ----------------------
try:
    import ncnn
    NCNN_AVAILABLE = True
except ImportError:
    NCNN_AVAILABLE = False
    print("警告：未安装 ncnn Python 包，人脸检测功能不可用。")
    print("请运行: pip3 install ncnn")

# ---------------------- 辅助函数 ----------------------
def get_next_filename(directory, extension):
    """获取下一个可用的文件名，数字从0开始递增"""
    if not os.path.exists(directory):
        os.makedirs(directory)
    files = [f for f in os.listdir(directory) if f.endswith(extension)]
    max_num = -1
    for f in files:
        num_part = f[:-len(extension)]
        if num_part.isdigit():
            max_num = max(max_num, int(num_part))
    return os.path.join(directory, f"{max_num + 1}{extension}")

def read_image(image_path):
    """读取本地图片，兼容包含中文或空格的路径。"""
    if not image_path:
        return None
    image_path = os.path.expanduser(image_path.strip().strip('"').strip("'"))
    if not os.path.exists(image_path):
        return None
    data = np.fromfile(image_path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

def write_image(image_path, image):
    """写入图片，兼容包含中文或空格的路径。"""
    extension = os.path.splitext(image_path)[1] or ".jpg"
    ok, encoded = cv2.imencode(extension, image)
    if not ok:
        return False
    encoded.tofile(image_path)
    return True

def get_detected_filename(source_path):
    """为本地测试图片生成不覆盖旧结果的标注图路径。"""
    if not os.path.exists(TEST_DIR):
        os.makedirs(TEST_DIR)
    base = os.path.splitext(os.path.basename(source_path))[0] or "image"
    candidate = os.path.join(TEST_DIR, f"{base}_detected.jpg")
    index = 1
    while os.path.exists(candidate):
        candidate = os.path.join(TEST_DIR, f"{base}_detected_{index}.jpg")
        index += 1
    return candidate

def choose_local_image():
    """优先弹出文件选择框；不可用时退回到终端输入路径。"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        image_path = filedialog.askopenfilename(
            title="选择一张本地图片进行人脸检测",
            initialdir=BASE_DIR,
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.bmp *.webp"),
                ("所有文件", "*.*"),
            ],
        )
        root.destroy()
        if image_path:
            return image_path
    except Exception as e:
        print(f"图形文件选择器不可用，将改为终端输入路径：{e}")

    return input("请输入本地图片完整路径（直接回车取消）：").strip()

def detect_image_file(face_detector, image_path, show_result=True):
    """使用当前模型检测一张本地图片，并保存标注结果。"""
    if face_detector is None:
        print("人脸检测器未加载，无法进行本地图片检测。")
        return None, []

    if not image_path:
        print("未选择图片。")
        return None, []
    image_path = os.path.abspath(os.path.expanduser(image_path.strip().strip('"').strip("'")))

    image = read_image(image_path)
    if image is None:
        print(f"无法读取图片，请检查路径或文件格式：{image_path}")
        return None, []

    start_time = time.time()
    annotated, boxes = face_detector.detect(image)
    detect_name = get_detected_filename(image_path)
    if not write_image(detect_name, annotated):
        print(f"检测已完成，但标注图保存失败：{detect_name}")
        return None, boxes

    elapsed = time.time() - start_time
    print(f"本地图片检测完成：{image_path}")
    print(f"检测到 {len(boxes)} 张人脸，用时 {elapsed:.3f} 秒，标注图保存为：{detect_name}")

    if show_result:
        cv2.namedWindow("Local Image Face Detection", cv2.WINDOW_NORMAL)
        cv2.imshow("Local Image Face Detection", annotated)
        cv2.waitKey(1)
    return detect_name, boxes

def create_face_detector(conf_threshold=0.5, debug=False):
    """初始化检测器，失败时返回 None，让主程序继续可用。"""
    if not NCNN_AVAILABLE:
        return None
    try:
        detector = FaceDetector(conf_threshold=conf_threshold, debug=debug)
        print("人脸检测模型加载成功。")
        return detector
    except Exception as e:
        print(f"加载人脸检测模型失败：{e}")
        return None

# ---------------------- SCRFD NCNN 推理相关 ----------------------
class FaceDetector:
    def __init__(self, param_path=MODEL_PARAM, bin_path=MODEL_BIN,
                 input_size=640, conf_threshold=0.5, iou_threshold=0.4,
                 debug=False):
        if not NCNN_AVAILABLE:
            raise RuntimeError("ncnn 不可用，无法初始化人脸检测器。")
        if not os.path.exists(param_path) or not os.path.exists(bin_path):
            raise FileNotFoundError(f"模型文件缺失：{param_path} / {bin_path}")

        self.net = ncnn.Net()
        self._configure_net()

        ret = self.net.load_param(param_path)
        if ret not in (0, None):
            raise RuntimeError(f"加载 param 失败，返回值 {ret}: {param_path}")
        ret = self.net.load_model(bin_path)
        if ret not in (0, None):
            raise RuntimeError(f"加载 bin 失败，返回值 {ret}: {bin_path}")

        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.debug = debug
        self.strides = (8, 16, 32)
        self.num_anchors = 2
        self.input_name = self._get_input_name(param_path)
        print(f"SCRFD 模型加载成功：{param_path}")
        print(f"NCNN 输入层: {self.input_name}")

    def _configure_net(self):
        """树莓派上优先使用 CPU，避免 Vulkan/线程配置导致底层异常。"""
        try:
            self.net.opt.use_vulkan_compute = False
            self.net.opt.num_threads = min(4, os.cpu_count() or 4)
        except Exception:
            pass

    def _decode_name(self, name):
        if isinstance(name, bytes):
            return name.decode("utf-8")
        return str(name)

    def _get_input_name(self, param_path):
        try:
            input_names = [self._decode_name(n) for n in self.net.input_names()]
            if input_names:
                return input_names[0]
        except Exception:
            pass

        try:
            with open(param_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[0] == "Input":
                        return parts[4]
        except Exception:
            pass
        return "input.1"

    def detect(self, bgr_img):
        """
        对 BGR 图片进行人脸检测。
        返回 (绘制框后的图片, 检测框列表)，检测框格式为：
        [x1, y1, x2, y2, confidence, landmarks]
        """
        original_h, original_w = bgr_img.shape[:2]
        input_img, scale, pad_x, pad_y = self._letterbox(bgr_img)
        mat_in = self._make_input_mat(input_img)

        ex = self.net.create_extractor()
        ret = ex.input(self.input_name, mat_in)
        if ret not in (0, None):
            raise RuntimeError(f"设置输入失败，输入层 {self.input_name}，返回值 {ret}")

        outputs = {}
        for stride in self.strides:
            for prefix in ("score", "bbox", "kps"):
                name = f"{prefix}_{stride}"
                ret, mat_out = ex.extract(name)
                if ret != 0:
                    raise RuntimeError(f"提取输出失败，输出层 {name}，返回值 {ret}")
                outputs[name] = mat_out

        boxes = self._postprocess(outputs, original_w, original_h, scale, pad_x, pad_y)
        annotated = bgr_img.copy()
        for box in boxes:
            x1, y1, x2, y2, conf = box[:5]
            landmarks = box[5] if len(box) > 5 else None
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label_y = max(15, y1 - 5)
            cv2.putText(annotated, f"{conf:.2f}", (x1, label_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            if landmarks is not None:
                for point in landmarks:
                    cv2.circle(annotated, (int(point[0]), int(point[1])), 2, (0, 0, 255), -1)
        return annotated, boxes

    def _letterbox(self, bgr_img):
        h, w = bgr_img.shape[:2]
        scale = min(self.input_size / w, self.input_size / h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        resized = cv2.resize(bgr_img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        canvas = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
        pad_x = 0
        pad_y = 0
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB), scale, pad_x, pad_y

    def _make_input_mat(self, rgb_img):
        rgb_img = np.ascontiguousarray(rgb_img)
        try:
            if hasattr(ncnn.Mat, "from_pixels"):
                mat_in = ncnn.Mat.from_pixels(
                    rgb_img.tobytes(),
                    ncnn.Mat.PixelType.PIXEL_RGB,
                    self.input_size,
                    self.input_size
                )
            else:
                raise AttributeError("ncnn.Mat.from_pixels 不可用")
        except (AttributeError, TypeError):
            mat_in = ncnn.Mat.from_pixels_resize(
                rgb_img.tobytes(),
                ncnn.Mat.PixelType.PIXEL_RGB,
                self.input_size,
                self.input_size,
                self.input_size,
                self.input_size
            )

        # SCRFD/InsightFace 预处理：RGB，减 127.5，再除以 128。
        mat_in.substract_mean_normalize([127.5, 127.5, 127.5],
                                        [1 / 128.0, 1 / 128.0, 1 / 128.0])
        return mat_in

    def _postprocess(self, outputs, img_w, img_h, scale, pad_x, pad_y):
        all_boxes = []
        all_scores = []
        all_landmarks = []

        for stride in self.strides:
            scores = self._mat_to_2d(outputs[f"score_{stride}"], 1).reshape(-1)
            bbox_preds = self._mat_to_2d(outputs[f"bbox_{stride}"], 4)
            kps_preds = self._mat_to_2d(outputs[f"kps_{stride}"], 10)

            centers = self._anchor_centers(stride)
            count = min(len(scores), len(bbox_preds), len(kps_preds), len(centers))
            scores = scores[:count]
            bbox_preds = bbox_preds[:count]
            kps_preds = kps_preds[:count]
            centers = centers[:count]

            keep = scores >= self.conf_threshold
            if self.debug:
                max_score = float(scores.max()) if scores.size else 0.0
                print(f"[debug] stride={stride}, outputs={count}, max_score={max_score:.4f}, keep={int(keep.sum())}")
            if not np.any(keep):
                continue

            scores = scores[keep]
            centers = centers[keep]
            bbox_preds = bbox_preds[keep] * stride
            kps_preds = kps_preds[keep] * stride

            boxes = self._distance_to_bbox(centers, bbox_preds)
            landmarks = self._distance_to_landmarks(centers, kps_preds)
            all_boxes.append(boxes)
            all_scores.append(scores)
            all_landmarks.append(landmarks)

        if not all_boxes:
            return []

        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)
        landmarks = np.concatenate(all_landmarks, axis=0)

        order = scores.argsort()[::-1]
        if order.size > 1000:
            order = order[:1000]
        boxes = boxes[order]
        scores = scores[order]
        landmarks = landmarks[order]

        keep = self._nms_indices(boxes, scores)
        detections = []
        for idx in keep:
            box = boxes[idx].copy()
            kps = landmarks[idx].copy()

            box[[0, 2]] = (box[[0, 2]] - pad_x) / scale
            box[[1, 3]] = (box[[1, 3]] - pad_y) / scale
            kps[:, 0] = (kps[:, 0] - pad_x) / scale
            kps[:, 1] = (kps[:, 1] - pad_y) / scale

            x1 = int(max(0, min(round(box[0]), img_w - 1)))
            y1 = int(max(0, min(round(box[1]), img_h - 1)))
            x2 = int(max(0, min(round(box[2]), img_w - 1)))
            y2 = int(max(0, min(round(box[3]), img_h - 1)))
            if x2 <= x1 or y2 <= y1:
                continue

            kps[:, 0] = np.clip(np.round(kps[:, 0]), 0, img_w - 1)
            kps[:, 1] = np.clip(np.round(kps[:, 1]), 0, img_h - 1)
            detections.append([x1, y1, x2, y2, float(scores[idx]), kps.astype(np.int32)])
        return detections

    def _anchor_centers(self, stride):
        feature_h = self.input_size // stride
        feature_w = self.input_size // stride
        grid_y, grid_x = np.mgrid[:feature_h, :feature_w]
        centers = np.stack((grid_x, grid_y), axis=-1).reshape(-1, 2).astype(np.float32)
        centers = centers * stride
        return np.repeat(centers, self.num_anchors, axis=0)

    def _distance_to_bbox(self, centers, distances):
        x1 = centers[:, 0] - distances[:, 0]
        y1 = centers[:, 1] - distances[:, 1]
        x2 = centers[:, 0] + distances[:, 2]
        y2 = centers[:, 1] + distances[:, 3]
        return np.stack((x1, y1, x2, y2), axis=-1)

    def _distance_to_landmarks(self, centers, distances):
        landmarks = np.empty((distances.shape[0], 5, 2), dtype=np.float32)
        for i in range(5):
            landmarks[:, i, 0] = centers[:, 0] + distances[:, i * 2]
            landmarks[:, i, 1] = centers[:, 1] + distances[:, i * 2 + 1]
        return landmarks

    def _mat_to_2d(self, mat_out, cols):
        arr = np.array(mat_out, dtype=np.float32)
        if arr.size == 0:
            return np.empty((0, cols), dtype=np.float32)
        if arr.ndim == 1:
            if arr.size % cols != 0:
                raise RuntimeError(f"输出长度 {arr.size} 不能按 {cols} 列解析")
            return arr.reshape(-1, cols)
        if arr.ndim == 2:
            if arr.shape[1] == cols:
                return arr
            if arr.shape[0] == cols:
                return arr.T
            if arr.size % cols == 0:
                return arr.reshape(-1, cols)
        if arr.ndim == 3:
            if arr.shape[-1] == cols:
                return arr.reshape(-1, cols)
            if arr.shape[0] == cols:
                return np.moveaxis(arr, 0, -1).reshape(-1, cols)
            if arr.shape[1] == cols:
                return np.moveaxis(arr, 1, -1).reshape(-1, cols)
            if arr.size % cols == 0:
                return arr.reshape(-1, cols)
        raise RuntimeError(f"无法解析输出维度 {arr.shape} 为 {cols} 列")

    def _nms_indices(self, boxes, scores):
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        order = scores.argsort()[::-1]
        keep = []

        while order.size > 0:
            i = order[0]
            keep.append(i)
            if order.size == 1:
                break

            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0, xx2 - xx1)
            h = np.maximum(0, yy2 - yy1)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
            order = order[np.where(iou <= self.iou_threshold)[0] + 1]
        return keep

def run_camera(face_detector):
    if not CAMERA_AVAILABLE:
        print(f"摄像头库不可用，无法启动摄像头界面：{CAMERA_IMPORT_ERROR}")
        print("如需只测试本地图片，可运行：python3 face_detect.py --image /path/to/image.jpg")
        return

    # ---------------------- 初始化摄像头和窗口 ----------------------
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": (1280, 720), "format": "RGB888"}
    )
    picam2.configure(config)

    picam2.set_controls({
        "AwbEnable": True,
        "AwbMode": controls.AwbModeEnum.Auto,
        "AeEnable": True,
        "Brightness": 0.05,
        "Contrast": 1.1,
        "Saturation": 1.15,
        "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Fast,
        "Sharpness": 1.2,
    })

    picam2.start()
    time.sleep(1)

    window_name = 'IMX219 - 拍照/录像/人脸检测'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)
    cv2.startWindowThread()

    recording = False
    out = None
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    fps = 30.0
    # 控制实时人脸检测的开关（默认关闭以保证流畅度）
    enable_live_face = False

    print("按键说明：")
    print("  p - 拍照 (自动进行人脸检测，并保存标注图片)")
    print("  i - 选择一张本地图片进行模型检测")
    print("  r - 开始/停止录像")
    print("  t - 切换实时人脸检测 (开关)")
    print("  q - 退出程序")

    try:
        while True:
            frame_rgb = picam2.capture_array()
            if frame_rgb is None:
                continue
            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR).copy()

            # 可选：实时人脸检测（开启后会显著降低帧率）
            if enable_live_face and face_detector is not None:
                try:
                    frame_bgr, live_boxes = face_detector.detect(frame_bgr)
                    if live_boxes:
                        cv2.putText(frame_bgr, f"Faces: {len(live_boxes)}", (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                except Exception as e:
                    enable_live_face = False
                    print(f"实时人脸检测失败，已自动关闭：{e}")

            cv2.imshow(window_name, frame_bgr)
            key = cv2.waitKey(1) & 0xFF

            if key == ord('p'):
                # 保存原始照片
                photo_name = get_next_filename(PHOTO_DIR, ".jpg")
                cv2.imwrite(photo_name, frame_bgr)
                print(f"照片已保存: {photo_name}")

                # 人脸检测并保存标注图片
                if face_detector is not None:
                    try:
                        annotated_img, boxes = face_detector.detect(frame_bgr)
                        detect_name = photo_name.replace(".jpg", "_detected.jpg")
                        cv2.imwrite(detect_name, annotated_img)
                        print(f"人脸检测完成，找到 {len(boxes)} 张人脸，标注图保存为: {detect_name}")
                    except Exception as e:
                        print(f"人脸检测失败，原始照片已保留：{e}")
                else:
                    print("人脸检测器未加载，无法进行检测。")

            elif key == ord('i'):
                image_path = choose_local_image()
                if image_path:
                    try:
                        detect_image_file(face_detector, image_path, show_result=True)
                    except Exception as e:
                        print(f"本地图片检测失败：{e}")
                else:
                    print("已取消本地图片检测。")

            elif key == ord('r'):
                if recording:
                    recording = False
                    out.release()
                    out = None
                    print("录像已停止并保存。")
                else:
                    movie_name = get_next_filename(MOVIE_DIR, ".mp4")
                    height, width = frame_bgr.shape[:2]
                    out = cv2.VideoWriter(movie_name, fourcc, fps, (width, height))
                    if not out.isOpened():
                        print("错误：无法创建视频文件！")
                        recording = False
                    else:
                        recording = True
                        print(f"开始录像: {movie_name}")

            elif key == ord('t'):
                enable_live_face = not enable_live_face
                if face_detector is not None:
                    status = "开启" if enable_live_face else "关闭"
                    print(f"实时人脸检测已{status}")
                else:
                    print("人脸检测器不可用，无法切换。")

            elif key == ord('q'):
                break

            if recording and out is not None:
                out.write(frame_bgr)

    finally:
        if out is not None:
            out.release()
        picam2.stop()
        cv2.destroyAllWindows()
        print("程序已退出。")

def parse_args():
    parser = argparse.ArgumentParser(description="IMX219 拍照/录像/SCRFD NCNN 人脸检测")
    parser.add_argument("-i", "--image", "--test-image", dest="test_image",
                        help="选择一张本地图片直接进行模型检测，不启动摄像头")
    parser.add_argument("--no-show", action="store_true",
                        help="本地图片检测后只保存结果，不弹出结果窗口")
    parser.add_argument("--conf", type=float, default=0.5,
                        help="人脸检测置信度阈值，默认 0.5；调试时可尝试 0.25")
    parser.add_argument("--debug", action="store_true",
                        help="打印 SCRFD 各输出层的最高分和保留数量，方便排查检测不到的问题")
    return parser.parse_args()

def main():
    args = parse_args()
    face_detector = create_face_detector(conf_threshold=args.conf, debug=args.debug)

    if args.test_image:
        detect_image_file(face_detector, args.test_image, show_result=not args.no_show)
        if not args.no_show:
            print("按任意键关闭检测结果窗口。")
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return

    run_camera(face_detector)

if __name__ == "__main__":
    main()
