# 第三方组件

Picamera2/libcamera用于树莓派相机采集，OpenCV/NumPy用于图像处理，ncnn用于端侧推理，gpiozero用于GPIO。具体组件及模型分别遵循上游许可。

公开人脸分支的模型名指向SCRFD格式，权重不在仓库中。YOLO/Ultralytics框架、MobileNetV4、BiFormer和训练数据不随本包转存；复用其代码或模型时应保留原许可和来源。

新增人体模型适配器通过Ultralytics的公开接口加载本地模型。Ultralytics适用其AGPL-3.0或商业许可条款，见[上游许可](https://github.com/ultralytics/ultralytics/blob/main/LICENSE)。本仓库MIT许可适用于自有应用与文档，不改变依赖、数据或模型的许可。
