# -*- coding: utf-8 -*-

# 复制为 config.local.py 后填写真实值。config.local.py 已被 .gitignore 排除，
# 不会进入版本库；config.py 加载时若存在 config.local.py 会自动覆盖同名大写变量。
#
# 本文件只作为配置模板，不要直接填写密钥。

# 监听列表：[微信昵称/群名, 使用的 prompt 文件名]
LISTEN_LIST = [["你的微信昵称", "角色1"]]

# 主模型（OpenAI 兼容接口）
DEEPSEEK_API_KEY = "YOUR_API_KEY"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"

# 视觉 / 表情识别模型（OpenAI 兼容接口）
MOONSHOT_API_KEY = "YOUR_API_KEY"
MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
MOONSHOT_MODEL = "moonshot-v1-8k"

# 联网搜索辅助模型
ONLINE_API_KEY = "YOUR_API_KEY"
ONLINE_BASE_URL = "https://api.deepseek.com/v1"
ONLINE_MODEL = "deepseek-chat"

# 辅助模型（记忆摘要等轻量任务）
ASSISTANT_API_KEY = "YOUR_API_KEY"
ASSISTANT_BASE_URL = "https://api.deepseek.com/v1"
ASSISTANT_MODEL = "deepseek-chat"

# WebUI 登录（默认关闭）
ENABLE_LOGIN_PASSWORD = False
LOGIN_PASSWORD = "YOUR_PASSWORD"
