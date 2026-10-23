import argparse
import json
import logging
import math
from pathlib import Path

from .counting import CountWindow, validate_polygon
from .server import AppServer
from .storage import Store
from .vision import LiveState, VisionWorker

DEFAULT = {"roi": [[0.05, 0.2], [0.95, 0.2], [0.95, 0.98], [0.05, 0.98]],
           "class_ids": [0], "confidence": 0.45, "window_size": 5, "max_age": 3.0,
           "poll_interval": 0.5, "width": 1280, "height": 720, "image_size": 640}


def configuration(path=None):
    config = dict(DEFAULT)
    if path:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) - set(config):
            raise ValueError("配置包含未知字段或不是JSON对象")
        config.update(value)
    config["roi"] = validate_polygon(config["roi"])
    CountWindow(config["window_size"])
    for key in ("confidence", "max_age", "poll_interval"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key}必须为正有限数")
    if config["confidence"] > 1 or config["poll_interval"] > config["max_age"]:
        raise ValueError("置信度不能超过1，采样间隔不能超过有效时长")
    for key in ("width", "height", "image_size"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 32 <= value <= 4096:
            raise ValueError(f"{key}需为32到4096之间的整数")
    ids = config["class_ids"]
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i < 0 for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("class_ids需为不重复的非负整数数组")
    return config


def main():
    parser = argparse.ArgumentParser(description="课堂人数核验与教师名单确认")
    parser.add_argument("--db", default="var/attendance.sqlite3")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", help="JSON配置文件")
    parser.add_argument("--model", help="已有本地人体模型.pt文件或NCNN目录")
    parser.add_argument("--source", help="picamera2 或 opencv:0；提供model时必须指定")
    args = parser.parse_args()
    if bool(args.model) != bool(args.source):
        parser.error("--model和--source必须同时提供；省略时只运行名单确认界面")
    if not 1 <= args.port <= 65535:
        parser.error("端口需在1到65535之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = configuration(args.config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    state = LiveState(config["max_age"])
    server = AppServer(("127.0.0.1", args.port), Store(args.db), state)
    worker = VisionWorker(state, args.model, args.source, config) if args.model else None
    try:
        if worker:
            worker.start()
        logging.info("打开 http://127.0.0.1:%s", args.port)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        logging.info("服务正在关闭")
    finally:
        if worker:
            worker.stop()
            worker.join(timeout=5)
            if worker.is_alive():
                logging.error("相机线程未在5秒内退出，请检查设备阻塞")
        server.server_close()


if __name__ == "__main__":
    main()
