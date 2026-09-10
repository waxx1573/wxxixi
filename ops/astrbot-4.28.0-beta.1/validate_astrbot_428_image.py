import os
from importlib.metadata import version

os.environ["MEM0_TELEMETRY"] = "false"

import astrbot
import funasr_onnx
import mem0
import modelscope
import onnxruntime
import playwright
import qdrant_client
from playwright.sync_api import sync_playwright

assert astrbot.__version__ == "4.28.0", astrbot.__version__
for name, expected in {
    "jsonpickle": "4.1.2",
    "networkx": "3.6.1",
    "asyncpg": "0.31.0",
    "posthog": "7.49.0",
    "modelscope-hub": "0.4.0",
    "scikit-learn": "1.9.0",
    "platformdirs": "4.11.7",
    "narwhals": "2.25.0",
    "onnxruntime": "1.29.0",

    "playwright": "1.62.0", "funasr-onnx": "0.4.2", "modelscope": "1.39.1",
    "openai": "3.6.0", "mem0ai": "1.0.11", "qdrant-client": "1.19.0", "protobuf": "6.33.6",
}.items():
    actual = version(name)
    assert actual == expected, f"{name}: expected {expected}, got {actual}"

with sync_playwright() as manager:
    browser = manager.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<title>ok</title>")
    assert page.title() == "ok"
    browser.close()

print("astrbot", astrbot.__version__)
print("playwright", playwright.__file__)
print("funasr_onnx", funasr_onnx.__file__)
print("modelscope", modelscope.__version__)
print("mem0ai", version("mem0ai"), mem0.__file__)
print("qdrant-client", version("qdrant-client"), qdrant_client.__file__)
print("protobuf", version("protobuf"))
print("onnxruntime", version("onnxruntime"), onnxruntime.__file__)
print("chromium", "ok")
