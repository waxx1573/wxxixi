# WeFlow 微信桥接 — OneBot v11 版

让微信小号接入 AstrBot，通过 **OneBot v11 协议** 与 AstrBot 通信。

## 架构

```
微信 ←→ WeFlow ──SSE──→ bridge.py ──WebSocket 客户端──→ AstrBot
                            ↑                              ↑
                          OneBot v11 事件             aiocqhttp 服务端
                          (ws://127.0.0.1:19777)    (0.0.0.0:19777)
```

bridge 以 WebSocket **客户端**连接 AstrBot 的 aiocqhttp **服务端**，与 NapCatQQ 连接 AstrBot 的方式完全一致。

## 前置条件

| 依赖 | 说明 |
|------|------|
| Windows 系统 | 需要桌面微信 |
| [WeFlow](https://weflow.top) | 已安装并登录微信，开启 API 服务（端口 5031） |
| Python 3.10+ | 运行桥接脚本 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 已部署运行的 AstrBot 实例 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.example.json config.json
# 然后编辑 config.json
```

```json
{
    "weflow_base_url": "http://127.0.0.1:5031",
    "access_token": "你的WeFlow_Access_Token",
    "astrbot_attachments": "C:\\astrbot\\data\\attachments",
    "bot_nicknames": ["机器人微信昵称"],
    "bot_wxid": "机器人自己的wxid_xxx",
    "send_method": "uia",
    "weflow_send_api": "http://127.0.0.1:5031/api/v1/message",
    "buffer_seconds": 5,
    "web_port": 8766,
    "group_reply_mode": "mention",
    "astrbot_ob_url": "ws://127.0.0.1:19777"
}
```

### 3. 配置 AstrBot

在 AstrBot 的配置中（WebUI 或 `cmd_config.json`）添加一个 aiocqhttp 平台适配器：

```json
{
    "id": "wechat_bridge",
    "type": "aiocqhttp",
    "enable": true,
    "ws_reverse_host": "0.0.0.0",
    "ws_reverse_port": 19777,
    "ws_reverse_token": ""
}
```

> 如果 AstrBot 运行在 Docker 中，需要将端口 19777 映射到宿主机：
> ```
> -p 19777:19777
> ```

重启 AstrBot。

### 4. 启动桥接

```bash
python main.py
```

Web 控制面板：`http://127.0.0.1:8766`

## 配置项说明

| 字段 | 说明 |
|------|------|
| `weflow_base_url` | WeFlow API 地址 |
| `access_token` | WeFlow API 的 Access Token |
| `bot_nicknames` | 机器人微信昵称，群聊 @ 检测用 |
| `bot_wxid` | 机器人自己的微信号 |
| `send_method` | `"uia"`（UIA 自动化，微信 4.0+ 推荐）或 `"weflow_api"` |
| `weflow_send_api` | WeFlow 发送消息 API 地址 |
| `buffer_seconds` | 消息缓冲秒数，多条消息合并后统一推送 |
| `web_port` | Web 控制面板端口 |
| `group_reply_mode` | `"mention"`（仅接收昵称提及）、`"all"`（全部接收）或 `"batch"`（批量转发）；普通消息均不添加合成 @ |
| `allowed_groups` | 群范围；优先填写 WeFlow 的群 session ID，也兼容完整群名。空列表沿用不限群的行为；修改后重启桥接器。 |
| `astrbot_ob_url` | AstrBot aiocqhttp WebSocket 服务端地址 |

## 工作原理

1. **接收消息**：连接 WeFlow SSE 推送，实时接收微信消息
2. **缓冲合并**：多消息缓冲 N 秒后合并推送
3. **OneBot 推送**：将合并后的消息转为 OneBot v11 消息事件，通过 WebSocket 推给 AstrBot
4. **AI 处理**：AstrBot 通过 aiocqhttp 接收事件，经过插件流水线处理后生成回复
5. **回复发送**：AstrBot 调用 `send_msg` API，bridge 收到后通过 UIA 或 WeFlow API 发回微信

## 发送模式

本地兼容修复：只有检测到真实昵称提及时才转换为 OneBot `at` 标记；`all` 与 `batch` 模式下的普通消息只转发原始内容，不合成唤醒。OneBot ID 使用 SHA-256 确定性映射，群 ID 基于 WeFlow session ID，重启和群改名后保持一致。由旧随机哈希版本升级时，需要同步更新 AstrBot 插件中明确允许的群 ID。

文本发送器使用已识别微信窗口的原生句柄，并在激活后和每次按键前确认微信仍在前台；激活或切群失败立即停止发送。完成按键后通过 WeFlow 查询目标会话，只有找到本次发送时间附近、正文匹配的 `isSend=1` 记录才日志确认文字已发送。OneBot 当前仍提前确认 API 请求，不能把 API 响应当成送达证据；搜索结果的目标会话校验和图片回读仍未完善。离线回归检查：`python -m unittest discover -s tests -v`。

图片描述可使用 `image_caption_fallbacks` 按顺序尝试备用 OpenAI 兼容接口；每项包含 `api_base`、`api_key` 和 `model`。运行配置含凭据，只保存在已忽略的 `config.json` 中。AstrBot 主回复的备用模型与直接调用的看图、压缩备用模型需要分别配置；当前统一使用 AstrBot 原生 Provider 配置，不再维护独立模型角色插件。

当前图片描述默认使用 qlsg Ollama 上的 `qwen2.5vl:3b`（具备 vision 能力）。Windows 桥接通过 SSH 本地转发访问 `http://127.0.0.1:61000`，不会将 Ollama 端口直接暴露到公网；`qwen3-embedding:0.6b` 仅用于向量检索，不能用于图片描述。视觉模型的实际响应速度和中文描述质量仍需在真实微信图片消息中继续验证。

| 模式 | 说明 |
|------|------|
| `uia` | Windows UI Automation 定位微信窗口，检查前台后用剪贴板和键盘快捷键搜索、发送 |
| `weflow_api` | 通过 WeFlow API 发送（需 WeFlow 端支持） |

## 文件结构

```
wechat-weflow-bridge-ob11/
├── main.py                 # 入口与生命周期管理
├── bridge_core.py          # 桥接核心（SSE 接收、消息缓冲、事件构造）
├── ob_client.py            # WebSocket 客户端（连接 AstrBot）
├── ob_protocol.py          # OneBot v11 协议处理
├── uia_sender.py           # UIA 纯键盘消息发送器
├── senders.py              # 发送器工厂
├── state.py                # 全局共享状态
├── config.py               # 配置加载
├── web_panel.py            # Web 控制面板（http://127.0.0.1:8766）
├── config.json             # 配置文件（已 gitignore）
├── config.example.json     # 配置示例
├── requirements.txt        # Python 依赖
├── start.bat               # Windows 快捷启动
├── CLAUDE.md               # 开发参考文档
├── LICENSE                 # MIT 许可证
└── README.md
```

## 许可证

MIT
