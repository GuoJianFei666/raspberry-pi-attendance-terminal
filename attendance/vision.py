"""Optional local YOLO model and Picamera2/OpenCV acquisition adapters."""
from datetime import datetime, timezone
import logging
from pathlib import Path
from threading import Event, Lock, Thread
import time

from .counting import CountWindow, Detection, select_people

LOG = logging.getLogger(__name__)


class LiveState:
    def __init__(self, max_age=3.0):
        self.max_age = max_age
        self.lock = Lock()
        self.data = {"state": "unavailable", "count": None, "raw_count": None,
                     "observed_at": None, "source": None, "sample_count": 0,
                     "error": None}
        self.updated = None
        self.jpeg = None

    def update(self, count, raw_count, samples, source, jpeg):
        with self.lock:
            self.data = {"state": "running", "count": count, "raw_count": raw_count,
                         "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "source": source, "sample_count": samples, "error": None}
            self.updated = time.monotonic()
            self.jpeg = jpeg

    def fail(self, error):
        with self.lock:
            self.data.update(state="error", count=None, raw_count=None,
                             sample_count=0, error=str(error))
            self.updated = None
            self.jpeg = None

    def snapshot(self):
        with self.lock:
            result = dict(self.data)
            if self.updated is not None and time.monotonic() - self.updated > self.max_age:
                result.update(state="stale", count=None, raw_count=None)
            return result

    def frame(self):
        with self.lock:
            if self.updated is None or time.monotonic() - self.updated > self.max_age:
                return None
            return self.jpeg


class YoloDetector:
    def __init__(self, model_path, class_ids, threshold, image_size):
        path = Path(model_path).expanduser()
        if not path.exists() or (path.is_file() and path.suffix != ".pt"):
            raise ValueError("需要已有本地.pt权重或NCNN模型目录，不自动下载模型")
        if path.is_dir() and not all((path / filename).is_file() for filename in
                                      ("model.ncnn.param", "model.ncnn.bin", "metadata.yaml")):
            raise ValueError("NCNN目录缺少模型文件或metadata.yaml")
        from ultralytics import YOLO
        self.model = YOLO(str(path.resolve()), task="detect")
        self.class_ids, self.threshold, self.image_size = class_ids, threshold, image_size
        names = self.model.names
        for class_id in class_ids:
            label = names.get(class_id, "") if isinstance(names, dict) else names[class_id]
            if str(label).lower() not in {"person", "human", "pedestrian", "人"}:
                raise ValueError("模型类别元数据必须将所选类别标为人体，不能加载face或数字占位类别模型")

    def predict(self, frame):
        result = self.model.predict(source=frame, classes=self.class_ids, conf=self.threshold,
                                    imgsz=self.image_size, device="cpu", verbose=False)[0]
        if result.boxes is None:
            raise RuntimeError("检测模型没有返回boxes字段")
        boxes = result.boxes
        return [Detection(*coords, float(score), int(label)) for coords, score, label in
                zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist())]


class Camera:
    def __init__(self, source, width, height):
        self.source, self.width, self.height = source, width, height
        self.handle = None

    def open(self):
        if self.source == "picamera2":
            from picamera2 import Picamera2
            self.handle = Picamera2()
            config = self.handle.create_video_configuration(main={"size": (self.width, self.height), "format": "RGB888"})
            self.handle.configure(config)
            self.handle.start()
        else:
            import cv2
            if not self.source.startswith("opencv:") or not self.source.split(":", 1)[1].isdigit():
                raise ValueError("摄像头来源必须为 picamera2 或 opencv:设备编号")
            self.handle = cv2.VideoCapture(int(self.source.split(":", 1)[1]))
            self.handle.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.handle.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            if not self.handle.isOpened():
                raise RuntimeError("摄像头打开失败")

    def read(self):
        if self.source == "picamera2":
            # Picamera2 RGB888 arrays are BGR byte triples, as expected by OpenCV/Ultralytics.
            return self.handle.capture_array("main")
        ok, frame = self.handle.read()
        if not ok:
            raise RuntimeError("摄像头未返回有效画面")
        return frame

    def close(self):
        if self.handle is not None:
            if self.source == "picamera2":
                try:
                    self.handle.stop()
                finally:
                    self.handle.close()
            else:
                self.handle.release()


class VisionWorker(Thread):
    def __init__(self, state, model_path, source, config):
        super().__init__(name="classroom-camera", daemon=True)
        self.state, self.model_path, self.source, self.config = state, model_path, source, config
        self.stop_event = Event()

    def run(self):
        camera = Camera(self.source, self.config["width"], self.config["height"])
        try:
            import cv2
            import numpy as np
            detector = YoloDetector(self.model_path, self.config["class_ids"],
                                    self.config["confidence"], self.config["image_size"])
            window = CountWindow(self.config["window_size"])
            camera.open()
            while not self.stop_event.is_set():
                frame = camera.read()
                if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
                    raise RuntimeError("相机图像必须为三通道BGR数组")
                height, width = frame.shape[:2]
                boxes = select_people(detector.predict(frame), width, height, self.config["roi"],
                                      self.config["confidence"], self.config["class_ids"])
                count = window.add(len(boxes))
                preview = frame.copy()
                polygon = np.array([(round(x * (width - 1)), round(y * (height - 1)))
                                    for x, y in self.config["roi"]], dtype=np.int32)
                cv2.polylines(preview, [polygon], True, (255, 170, 0), 2)
                for box in boxes:
                    cv2.rectangle(preview, (int(box.x1), int(box.y1)), (int(box.x2), int(box.y2)), (60, 220, 100), 2)
                cv2.putText(preview, f"Visible: {count}", (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 220, 100), 2)
                ok, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    raise RuntimeError("预览图像编码失败")
                self.state.update(count, len(boxes), len(window.values), self.source, jpeg.tobytes())
                self.stop_event.wait(self.config["poll_interval"])
        except Exception as exc:
            LOG.exception("视觉采集已停止")
            self.state.fail(exc)
        finally:
            try:
                camera.close()
            except Exception as exc:
                LOG.exception("摄像头释放失败")
                self.state.fail(exc)

    def stop(self):
        self.stop_event.set()
