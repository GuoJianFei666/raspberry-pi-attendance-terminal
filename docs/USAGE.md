# 使用说明

## 名单确认与记录

需要Python 3.10及以上。仓库根目录运行 `python -m attendance` 后，打开 `http://127.0.0.1:8765`。基础服务仅使用Python标准库，不需要摄像头或模型。

导入UTF-8 CSV，第一行为 `student_id,name`。使用 [roster.example.csv](../examples/roster.example.csv) 时，记录仅用于功能检查。填写课程、班级、确认教师和时间后创建草稿，逐项选择到课、缺勤、迟到或请假，确认保存。名单漏项会阻止保存，已确认课程不能覆盖。查询框支持课程或班级关键字，记录可导出CSV。

数据库默认写入 `var/attendance.sqlite3`，可用 `--db` 指定本机位置。更换端口使用 `--port 8766`。服务只监听127.0.0.1，不提供多用户远程访问。退出使用Ctrl+C。

## 人体模型与摄像头

视觉模块另需Ultralytics、OpenCV、NCNN及其依赖。可在独立Python环境安装 `python -m pip install -e ".[vision]"`。Picamera2与libcamera应使用Raspberry Pi OS的相机环境；安装方法以[Raspberry Pi Picamera2文档](https://github.com/raspberrypi/picamera2)为准。当前开发环境未安装这些视觉依赖。

人体模型由使用者放在本地。NCNN目录须包含 `model.ncnn.param`、`model.ncnn.bin` 与 `metadata.yaml`；类别名称必须能识别所选人体类别。原有人脸权重、SCRFD权重，以及80个数字占位类别的早期导出目录均不适合作为本应用人体模型。导出与加载方式参照[Ultralytics NCNN文档](https://docs.ultralytics.com/integrations/ncnn/)。本应用不执行模型导出或量化。

```console
python -m attendance --config config.example.json --model /path/to/yolo11n_ncnn_model --source picamera2
```

本地`.pt`人体模型同样可作为 `--model` 参数；USB设备使用 `--source opencv:0`。Picamera2选择 `RGB888` 输出，其数组字节顺序为BGR，可直接交给OpenCV和Ultralytics，依据[Picamera2手册](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)附录A。

## 座位区域与人数窗口

[config.example.json](../config.example.json) 中 `roi` 为0到1之间的画面坐标，原点位于左上角。按边界顺序填写凸多边形，覆盖学生座位并避开讲台。人体框底边中心落在区域内才计数。检测坐标随画面宽高归一化，改变分辨率时保留相同区域定义。

`confidence` 控制最低检测置信度，`class_ids` 指定人体类别；`window_size` 控制最近多少帧参与人数中位数计算，偶数帧时取较低中位数。`poll_interval` 为推理后等待时长，实际更新间隔还包括采集与推理耗时。`max_age` 为观察值有效时长，部署时需按实际更新间隔调整。示例参数用于启动配置，不表示经过课堂效果优化。

## 原有Pi5脚本

- `python3 src/pi5/camera_display.py`：按 `p` 拍照、`r` 开始或停止录像、`q` 退出。照片和录像写入运行目录下的 `photos`、`movies` 目录。
- `face_detect.py`：相机与本地图片模式共用SCRFD / NCNN模型，默认权重位于脚本同级 `downloaded_model/`，名为 `scrfd_500m_bnkps_shape640x640.opt.param` 与同名 `.bin`。本地图片可用 `python3 src/pi5/face_detect.py --image /path/to/image.jpg --no-show`，其余参数见源码末尾。
- `play_buzzer.py`：使用gpiozero与BCM17无源蜂鸣器，独立播放音符。该脚本未接入新增名单确认服务。

这些脚本依赖对应原有环境，本次整理保留字节且未运行。相机与人体模型的本版本验证范围见[版本记录](CHANGELOG.md)。
