# wxxixi

基于 [wechatbot-new](https://github.com/fanyuantaier/wechatbot-new) 的微信智能聊天机器人分支：保留上游角色扮演、记忆、表情包、定时提醒、联网搜索和 WebUI 配置能力，并在此基础上新增微信 4.1.x 数据库兼容补丁和平台无关的 MVP 消息管线。

> 本项目是上游项目的 GPL-3.0 派生作品，开源依据见 [LICENSE](LICENSE) 和 [LICENSE_COMPLIANCE.md](LICENSE_COMPLIANCE.md)。上游原始说明保留在 [README_UPSTREAM.md](README_UPSTREAM.md)。

## 功能

- 微信 4.1.x 消息收发，支持私聊与群聊监听
- 多用户 / 多群独立 Prompt，按微信昵称或群名路由
- DeepSeek 等 OpenAI 兼容大模型接入，主模型、视觉、联网、辅助模型分别配置
- 图片与表情识别、情绪表情包回复、主动消息、定时与语音提醒
- 记忆摘要、敏感内容清理、链接正文抓取、联网搜索
- Flask WebUI：配置编辑、Prompt 管理、角色论坛
- `mvp_core`：平台无关消息模型、SQLite 归档与去重、三级权限、阶段一决策管线
- `mvp_core/wechatauto_compat.py`：微信 4.1.x WAL 多代合并、输入框探测与连续发送兼容补丁

## 快速开始

环境要求：Windows 10/11、微信 4.1.x 桌面版、Python 3.9+。

```powershell
git clone https://github.com/waxx1573/wxxixi.git
cd wxxixi
pip install -r requirements.txt
```

1. 登录电脑微信并保持后台运行。
2. 复制 `config.example.py` 为 `config.local.py`，填写 API Key、模型和监听昵称。
3. 启动 WebUI：

   ```powershell
   python config_editor.py
   ```

4. 浏览器打开 `http://127.0.0.1:5000` 修改配置并启动机器人；也可以直接运行 `python bot.py`。

`wechatauto-replica` 依赖请按项目首页说明安装对应版本，`requirements.txt` 已声明最低版本。

## 配置说明

- `config.py`：默认配置与占位值，可安全入库。
- `config.local.py`：本机真实配置，被 `.gitignore` 排除；存在时会覆盖 `config.py` 中的同名大写变量。
- `prompts/`：每个文件对应一个角色 Prompt，`LISTEN_LIST` 第二项指定文件名。

常用开关：

| 配置项 | 说明 |
| --- | --- |
| `ENABLE_MVP_STAGE1` | 启用阶段一消息管线（归档 / 去重 / 权限 / 决策） |
| `ENABLE_GROUP_AT_REPLY` | 群聊中 @ 机器人时回复 |
| `ENABLE_GROUP_KEYWORD_REPLY` | 群聊命中关键词时回复 |
| `GROUP_CHAT_RESPONSE_PROBABILITY` | 群普通消息回复概率（0-100） |
| `ACCEPT_ALL_GROUP_CHAT_MESSAGES` | 接收全部群聊消息（配合上述开关使用） |

## MVP 管线

`mvp_core/` 是与微信适配层解耦的独立模块，可通过 unittest 验证：

```powershell
python -m unittest discover -t . -s tests -p "test_*.py" -v
```

阶段一真实微信验收记录见 [CHANGELOG.md](CHANGELOG.md) 与仓库内 `tests/manual_acceptance_*.py`。当前已验证：真实群文本接收与归档、单条真实文本发送（含同进程连续发送）、监听群普通消息自动回复、机器人自己消息屏蔽。私聊、媒体消息、重启去重和三级权限的真实验收仍在进行中。

## 上游与版权

- 上游项目：[fanyuantaier/wechatbot-new](https://github.com/fanyuantaier/wechatbot-new)（GPL-3.0-or-later）
- 原始项目：[KouriChat/KouriChat](https://github.com/KouriChat/KouriChat)
- 微信自动化依赖：`wechatauto-replica`（Apache-2.0，微信 4.x 适配复刻）

本项目以 GPL-3.0 或更高版本发布。使用、修改或分发时请遵守 GPL-3.0 条款，并保留上游版权声明。

## 安全提醒

- 不要把 API Key、微信登录状态和聊天记录提交到仓库。
- 机器人用于自动化收发微信消息，请遵守微信平台规则，自行承担使用风险。
