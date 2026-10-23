# 树莓派视觉考勤终端

统计教室学生座位区域内的可见人数，由教师对照班级名单确认到课、缺勤、迟到和请假状态，再保存本节课记录。视觉计数与个人考勤分别记录，遮挡或离席时由教师核对。

**应用**：Python 3.10+ · SQLite · HTTP / JSON · HTML / CSS / JavaScript。  
**视觉与设备**：YOLO11n · NCNN · Picamera2 / libcamera · OpenCV · 树莓派5。

## 直接运行名单确认

在仓库根目录执行，无须安装第三方包：

```console
python -m attendance --db var/attendance.sqlite3
```

打开 `http://127.0.0.1:8765`，填写课程、班级、教师和时间，导入表头为 `student_id,name` 的CSV名单。可用 [示例名单](examples/roster.example.csv) 检查功能，其中三位学生均为示例。逐项确认状态后保存，随后可按课程或班级查询并导出CSV。未启用摄像头时，人数显示为空，保留完整人工确认流程。

## 接入人体计数

配置好树莓派相机环境及视觉依赖后，使用已有本地人体检测模型启动：

```console
python -m attendance --db var/attendance.sqlite3 --config config.example.json --model /path/to/yolo11n_ncnn_model --source picamera2
```

USB摄像头可用 `--source opencv:0`。模型支持本地 `.pt` 文件或含模型与元数据的NCNN目录，所选类别须标为 `person`、`human`、`pedestrian` 或 `人`。程序检查本地路径，不自动下载权重。[使用说明](docs/USAGE.md)列出依赖、模型条件和座位区域配置。

- 人体框底边中心落在座位区域内才计数，排除区域外的教师与走廊人员。
- 最近若干帧的人数取中位数，减小单帧波动；接口保留原始人数、采样时间和窗口帧数。
- 过期画面或设备错误显示为不可用；有效画面的零人数单独处理。
- 教师必须确认完整名单。SQLite事务同时保存各人状态、确认时间与有效人数观察值，重复提交返回冲突。
- 检测预览仅保留在内存。CSV名单和SQLite数据库留在本机，CSV导出对公式起始字符作转义。

## 源码与阅读入口

| 文件 | 具体职责 |
| --- | --- |
| [attendance/counting.py](attendance/counting.py) | 座位区域、人体框筛选与窗口计数 |
| [attendance/vision.py](attendance/vision.py) | 本地YOLO模型、相机适配、预览与设备状态 |
| [attendance/storage.py](attendance/storage.py) | 名单校验、SQLite事务、查询与CSV导出 |
| [attendance/server.py](attendance/server.py)、[web/](attendance/web/index.html) | 本地HTTP接口与教师确认界面 |
| [src/pi5/camera_display.py](src/pi5/camera_display.py) | 原有相机预览、拍照与录像脚本 |
| [src/pi5/face_detect.py](src/pi5/face_detect.py) | 原有SCRFD / NCNN人脸检测分支 |
| [src/pi5/play_buzzer.py](src/pi5/play_buzzer.py) | 原有BCM17无源蜂鸣器音符控制 |
| [tests/test_attendance.py](tests/test_attendance.py) | 计数、名单、持久化、并发确认与HTTP流程检查 |

[架构](docs/ARCHITECTURE.md) · [接口](docs/API.md) · [版本记录](docs/CHANGELOG.md) · [数据与模型版本](docs/DATA-AND-VERSIONS.md) · [源码清单](source-manifest.json)

## 版本与验证

`attendance/` 为2026年10月23日本次公开整理中补充的应用代码。`src/pi5/` 三份脚本按原始字节保留。历史项目包含YOLO11n人脸分支的MobileNetV4、BiFormer与NCNN / INT8部署工作；本次新增应用通过人体检测适配器完成课堂计数，两者对应不同版本与用途。

新增应用已在Windows、Python 3.12下通过22项自动检查，并完成示例名单的浏览器操作检查。测试使用合成名单与检测框。摄像头、YOLO / NCNN推理及树莓派性能尚未在本版本中实测，未发布FPS或精度结果。

```console
python -m unittest discover -s tests -v
```

已有face数据快照为train 13,386张、val 3,347张，合计16,733张、51,846个face框。该统计对应历史训练材料，不是本应用的课堂人数或模型精度。真实课堂图像、学生名单、训练集与模型权重不随仓库分发。

本仓库自有代码与文档采用 [MIT许可](LICENSE)，第三方代码、数据和模型适用各自许可，见[第三方说明](THIRD_PARTY_NOTICES.md)。
