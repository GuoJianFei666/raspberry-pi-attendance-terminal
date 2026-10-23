import cv2
from picamera2 import Picamera2
from libcamera import controls
import time
import os

# ---------------------- 辅助函数 ----------------------
def get_next_filename(directory, extension):
    if not os.path.exists(directory):
        os.makedirs(directory)
    files = [f for f in os.listdir(directory) if f.endswith(extension)]
    max_num = -1
    for f in files:
        num_part = f[:-len(extension)]
        if num_part.isdigit():
            max_num = max(max_num, int(num_part))
    return os.path.join(directory, f"{max_num + 1}{extension}")

# ---------------------- 初始化摄像头 ----------------------
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

# ---------------------- 窗口显示设置 ----------------------
WINDOW_NAME = 'IMX219 - 实时画面'
# 显式创建窗口，并允许调整大小
cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
# 可选：设置窗口初始大小
cv2.resizeWindow(WINDOW_NAME, 1280, 720)
# 启动独立的窗口线程（某些环境下可避免界面冻结）
cv2.startWindowThread()

# ---------------------- 录像变量 ----------------------
recording = False
out = None
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
fps = 30.0

print("按键说明：")
print("  p - 拍照 (保存到 photos 文件夹)")
print("  r - 开始/停止录像 (保存到 movies 文件夹)")
print("  q - 退出程序")

try:
    while True:
        # 捕获帧 (RGB)
        frame_rgb = picam2.capture_array()
        if frame_rgb is None:
            print("警告：捕获帧为空")
            continue

        # 转换为 BGR 并确保内存连续（修复 imshow 可能不显示的问题）
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR).copy()

        # 显示画面
        cv2.imshow(WINDOW_NAME, frame_bgr)

        # 按键处理
        key = cv2.waitKey(1) & 0xFF

        if key == ord('p'):
            filename = get_next_filename("photos", ".jpg")
            cv2.imwrite(filename, frame_bgr)
            print(f"照片已保存: {filename}")

        elif key == ord('r'):
            if recording:
                recording = False
                out.release()
                out = None
                print("录像已停止并保存。")
            else:
                filename = get_next_filename("movies", ".mp4")
                height, width = frame_bgr.shape[:2]
                out = cv2.VideoWriter(filename, fourcc, fps, (width, height))
                if not out.isOpened():
                    print("错误：无法创建视频文件！")
                    recording = False
                else:
                    recording = True
                    print(f"开始录像: {filename}")

        elif key == ord('q'):
            break

        # 写入录像帧
        if recording and out is not None:
            out.write(frame_bgr)

finally:
    if out is not None:
        out.release()
    picam2.stop()
    cv2.destroyAllWindows()
    print("程序已退出。")