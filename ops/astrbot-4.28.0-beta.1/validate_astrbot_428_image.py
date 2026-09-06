from pathlib import Path

import funasr_onnx
import modelscope
import playwright
from playwright.sync_api import sync_playwright

version_text = ""
for path in Path("/AstrBot").rglob("version.py"):
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "4.28.0" in text:
        version_text = "4.28.0-beta.1"
        break

with sync_playwright() as manager:
    browser = manager.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<title>ok</title>")
    assert page.title() == "ok"
    browser.close()

print("astrbot", version_text or "version-file-not-found")
print("playwright", playwright.__file__)
print("funasr_onnx", funasr_onnx.__file__)
print("modelscope", modelscope.__version__)
print("chromium", "ok")
