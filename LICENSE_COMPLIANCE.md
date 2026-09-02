# 许可证合规说明 / License Compliance Guide

## 项目许可证 / Project License

本项目（wxxixi）是 [wechatbot-new](https://github.com/fanyuantaier/wechatbot-new)
的派生作品，遵循 **GNU GPL-3.0 或更高版本** 许可证。完整文本见 [LICENSE](LICENSE)。

## 依赖库许可证 / Dependencies License

本项目使用的第三方依赖及其许可证：

| 依赖 | 许可证 | 用途 |
| --- | --- | --- |
| wechatauto-replica | Apache License 2.0 | 微信 4.x 自动化核心库（消息读取 / 坐标+OCR 发送） |
| Flask | BSD 3-Clause | WebUI 框架 |
| werkzeug | BSD 3-Clause | WSGI 工具库 |
| openai | Apache License 2.0 | AI 模型接口 |
| requests | Apache License 2.0 | HTTP 请求 |
| beautifulsoup4 | MIT | HTML 解析 |
| lxml | BSD | BeautifulSoup 解析器 |
| psutil | BSD 3-Clause | 系统进程监控 |
| filelock | Unlicense | 文件锁 |

上述许可证均与 GPL-3.0 兼容。`wechatauto-replica` 的传递依赖
（uiautomation / pywin32 / pyperclip / Pillow / psutil / colorama / cryptography
/ winsdk / pypinyin 等）由其自动带入，不在 `requirements.txt` 中重复声明。

## GPL-3.0 合规性 / GPL-3.0 Compliance

1. 所有 GPL 覆盖的代码均提供源代码。
2. 修改必须遵循 GPL-3.0 条款，并保留上游版权声明。
3. 分发本项目时必须包含完整的源代码和许可证文件。
4. 依赖库许可证均为 GPL-3.0 兼容许可。

## 分发说明 / Distribution Notes

- 本项目基于 [KouriChat](https://github.com/KouriChat/KouriChat) 及
  [wechatbot-new](https://github.com/fanyuantaier/wechatbot-new) 修改，
  上游版权归属和修改声明见源码文件头及 [README_UPSTREAM.md](README_UPSTREAM.md)。
- 重新分发时请保留 LICENSE、版权声明和本说明文件。

---
更新日期：2026年9月
