# 本地服务接口

服务默认监听 `http://127.0.0.1:8765`，界面与接口使用同一地址。写入使用 JSON；接口返回的时间带时区，记录采用 UTC。系统只有教师确认后的个人考勤状态，不执行身份识别。

| 请求 | 用途 |
| --- | --- |
| `GET /api/live` | 当前计数、原始单帧人数、采样时间、窗口帧数和设备状态 |
| `GET /api/frame.jpg` | 内存中的最新标注画面；不可用或过期返回404 |
| `GET /api/sessions?q=课程或班级` | 查询最近200条记录 |
| `POST /api/sessions` | 用名单快照创建课程草稿 |
| `GET /api/sessions/{id}` | 查看课程与完整名单状态 |
| `POST /api/sessions/{id}/confirm` | 逐项确认名单，一次事务保存 |
| `GET /api/sessions/{id}/export.csv` | 导出带UTF-8 BOM的CSV |

创建课程需要 `title`、`class_name`、`teacher`、带时区的 `scheduled_at`，以及表头为 `student_id,name` 的 `roster_csv` 字符串。学号按字符串保存，保留前导零。重复学号、空名单、格式错误和超过1000人的名单会被拒绝。

确认请求包含读取时的 `revision`、完整 `statuses` 字典和可选 `note`。字典键为学号，值为 `present`、`absent`、`late` 或 `excused`。`pending` 必须由教师处理后才能保存。

```json
{
  "revision": 1,
  "statuses": {"EX001": "present", "EX002": "late"},
  "note": "示例数据，仅用于功能检查"
}
```

确认时，服务在 `BEGIN IMMEDIATE` 事务中核对状态与版本，再同时写入所有学生状态、确认时间及当前有效的视觉观察值。重复提交或已确认记录返回409。名单漏项、额外学号或非法状态返回400，并保留原草稿。课程不存在返回404。数据库异常返回500并记录日志。

设备状态为 `unavailable`、`running`、`stale` 或 `error`。只有 `running` 的人数可作为有效观察值；其余状态的 `count` 为 `null`。无视觉人数时，教师仍可确认名单，记录中的 `observed_count` 保持空值。

服务仅支持本机单终端使用，不提供远程教师账号、权限分级或校级平台接入。Host与Origin检查用于拒绝其他站点向本地接口发起请求。真实名单与数据库保存在本机，不放入版本库。
