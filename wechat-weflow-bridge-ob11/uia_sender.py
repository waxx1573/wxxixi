"""
uia_sender.py — 校验目标会话和输入焦点的微信消息发送器
=================================================================

原理：
  UIA 定位会话列表和聊天输入框，剪贴板配合键盘输入消息。

工作流：
  1. 定位微信窗口并激活到前台
  2. 在会话列表精确选中目标，核验聊天输入框
  3. 剪贴板复制消息 → Ctrl+V 粘贴 → Enter 发送
  4. 图片通过 PowerShell 复制到剪贴板 → Ctrl+V → Enter

依赖:
  pip install uiautomation pyperclip
  发送图片需要 PowerShell (Windows 自带)
"""

import logging
from contextlib import contextmanager
import os
import random
import subprocess
import threading
import time

log = logging.getLogger("weflow-bridge")


class BaseSender:
    """消息发送器基类"""
    def send_text(self, contact: str, text: str) -> bool:
        raise NotImplementedError

    def send_image(self, contact: str, image_path: str) -> bool:
        raise NotImplementedError


class UiaSender(BaseSender):
    """
    UIA 精确定位会话，核验前台和输入焦点后发送。
    """

    WECHAT_TITLES = ["微信", "WeChat"]
    WECHAT_EXECUTABLE = r"C:\app\Weixin\Weixin.exe"

    def __init__(self, search_enabled: bool = True):
        self._lock = threading.Lock()
        self._auto = None
        self._ready = False

        # 微信窗口
        self._window = None

        # 最近联系人缓存（相同目标跳过搜索，快速发送）
        self._last_contact = ""
        self._launch_attempted_at = 0.0

        self.search_enabled = search_enabled

        self._init()

    # ================================================================
    # 初始化
    # ================================================================

    def _init(self):
        """初始化 uiautomation 并定位微信窗口"""
        try:
            import uiautomation as auto
            self._auto = auto
        except ImportError:
            log.error("请先安装 uiautomation: pip install uiautomation")
            return

        log.info("正在搜索微信窗口...")
        self._find_window()
        if not self._window:
            self._launch_wechat()
        if self._window:
            log.info(f"微信窗口: '{self._window.Name}' ClassName={self._window.ClassName}")
            self._ready = True

    def _launch_wechat(self):
        """Start WeChat when the bridge runs before the desktop client."""
        now = time.time()
        if now - self._launch_attempted_at < 30:
            return
        if not os.path.isfile(self.WECHAT_EXECUTABLE):
            log.warning("未找到微信程序: %s", self.WECHAT_EXECUTABLE)
            return
        self._launch_attempted_at = now
        try:
            subprocess.Popen([self.WECHAT_EXECUTABLE], close_fds=True)
            for _ in range(30):
                time.sleep(0.5)
                self._find_window()
                if self._window:
                    log.info("已自动启动并发现微信窗口")
                    return
        except Exception as exc:
            log.error("自动启动微信失败: %s", type(exc).__name__)

    def _find_window(self):
        """只接受已支持的微信主窗口，不匹配搜一搜等附属窗口。"""
        auto = self._auto
        self._window = None
        root = auto.GetRootControl()
        for w in root.GetChildren():
            if w.ClassName in ("mmui::MainWindow", "WeChatMainWndForPC") and w.Name in self.WECHAT_TITLES:
                self._window = w
                return

    @contextmanager
    def _automation_session(self):
        if self._auto is None:
            yield
            return
        # UIA controls cannot be shared between the main thread and send workers.
        with self._auto.UIAutomationInitializerInThread():
            self._find_window()
            self._ready = self._window is not None
            try:
                yield
            finally:
                self._window = None

    def _ensure_window(self) -> bool:
        """确保窗口可用"""
        if not self._ready:
            return False
        if self._window and self._window.Exists(0.2):
            return True
        self._find_window()
        if not self._window:
            log.warning("微信窗口未找到")
            self._ready = False
            return False
        return True

    def _activate(self) -> bool:
        """确认微信成为前台窗口；失败时禁止后续键盘输入。"""
        try:
            import ctypes
            hwnd = self._window.NativeWindowHandle
            if not hwnd:
                return False
            self._window.SetActive()
            if self._auto.GetForegroundWindow() == hwnd:
                return True
            self._auto.SwitchToThisWindow(hwnd)
            time.sleep(0.2)
            if self._auto.GetForegroundWindow() == hwnd:
                return True
            user32 = ctypes.windll.user32
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            foreground = self._auto.GetForegroundWindow()
            foreground_tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None)
            target_tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
            attached = []
            try:
                for tid in {foreground_tid, target_tid} - {current_tid, 0}:
                    if user32.AttachThreadInput(current_tid, tid, True):
                        attached.append(tid)
                self._auto.ShowWindow(hwnd, self._auto.SW.Restore)
                self._auto.BringWindowToTop(hwnd)
                self._auto.SetForegroundWindow(hwnd)
            finally:
                for tid in attached:
                    user32.AttachThreadInput(current_tid, tid, False)
            time.sleep(0.2)
            if self._auto.GetForegroundWindow() == hwnd:
                return True
        except Exception as exc:
            log.error("微信激活异常: %s", type(exc).__name__)
        log.error("微信未成为前台窗口，已停止发送")
        return False

    def _require_foreground(self):
        hwnd = getattr(self, "_active_hwnd", None) or self._window.NativeWindowHandle
        if self._auto.GetForegroundWindow() != hwnd:
            raise RuntimeError("微信失去前台焦点，已停止键盘输入")

    def _require_focus(self, control):
        self._require_foreground()
        focused = self._auto.GetFocusedControl()
        if focused is None or focused.GetRuntimeId() != control.GetRuntimeId():
            raise RuntimeError("微信输入焦点不在已核验控件，已停止输入")

    def _named_control(self, automation_id):
        roots = [self._window]
        # WeChat 4.x may render the active chat in a separate top-level window.
        try:
            roots.extend(
                w for w in self._auto.GetRootControl().GetChildren()
                if w is not self._window and (
                    w.ClassName == "ChatSingleWindow" or
                    (w.ClassName == "Chrome_WidgetWin_1" and "ChatSingleWindow" in (w.AutomationId or ""))
                )
            )
        except Exception:
            pass
        for root in roots:
            control = root.Control(AutomationId=automation_id, searchDepth=12)
            if control.Exists(0.5):
                return control
        # New WeChat nests the chat pane below custom controls; search children
        # explicitly when the top-level query does not cross that boundary.
        def walk(parent, depth=0):
            if depth > 14:
                return None
            for child in parent.GetChildren():
                if child.AutomationId == automation_id:
                    return child
                found = walk(child, depth + 1)
                if found:
                    return found
            return None
        for root in roots:
            found = walk(root)
            if found:
                return found
        return None

    def _session_list(self):
        """Locate the session list even when WeChat nests it below custom views."""
        sessions = self._window.ListControl(AutomationId="session_list", searchDepth=12)
        if sessions.Exists(0.5):
            return sessions
        return self._named_control("session_list")

    def _send_keys(self, keys, control=None):
        self._require_foreground()
        if control is not None:
            self._require_focus(control)
        self._auto.SendKeys(keys)

    # ================================================================
    # 联系人切换
    # ================================================================

    def _switch_contact(self, contact: str) -> bool:
        """
        切换到指定联系人/群聊的聊天窗口。

        只选择会话列表中的精确匹配项；不存在或重名时停止。
        """
        if not self._ensure_window():
            return False
        if not self._activate():
            return False

        try:
            sessions = self._session_list()
            if sessions is None or not sessions.Exists(1):
                log.error("微信会话列表不可用，已停止发送")
                return False
            matches = [item for item in sessions.GetChildren()
                       if item.AutomationId == "session_item_" + contact]
            if len(matches) != 1:
                log.error("目标会话不唯一或不可见，已停止发送: %s", contact)
                return False
            item = matches[0]
            self._require_foreground()
            selection = item.GetSelectionItemPattern()
            try:
                selection.Select()
            except Exception:
                pass
            if not item.SetFocus():
                return False
            # A single UIA click selects the session without opening a
            # separate ChatSingleWindow. Do not send Enter: WeChat 4.x treats
            # it as "open independent chat window".
            item.Click()

            title_id = "title_h_view.title_left_v_view_.title_left_info_v_view_.big_title_line_h_view.current_chat_name_label"
            title = editor = None
            for _ in range(10):
                time.sleep(0.2)
                title = self._named_control(title_id)
                editor = self._named_control("chat_input_field")
                if title is not None and title.Name == contact and editor is not None:
                    break
            if title is None or title.Name != contact or editor is None:
                focused = self._auto.GetFocusedControl()
                log.error(
                    "目标会话或聊天输入框校验失败: %s (title=%r editor=%s focused=%r/%r)",
                    contact,
                    title.Name if title is not None else None,
                    editor is not None,
                    focused.Name if focused is not None else None,
                    focused.AutomationId if focused is not None else None,
                )
                return False
            editor_hwnd = getattr(editor, "NativeWindowHandle", None)
            if isinstance(editor_hwnd, int) and editor_hwnd:
                self._active_hwnd = editor_hwnd
            else:
                self._active_hwnd = self._window.NativeWindowHandle
            if not editor.SetFocus():
                return False
            if self._auto.GetFocusedControl().GetRuntimeId() != editor.GetRuntimeId():
                log.error("聊天输入框未获得焦点: %s", contact)
                return False
            self._input_control = editor

            log.info("已选择目标会话，等待输入框校验: %s", contact)
            return True
        except Exception as exc:
            log.error("切换联系人失败: %s", type(exc).__name__)
            return False

    # ================================================================
    # 发送文字 (纯键盘)
    # ================================================================

    def send_text(self, contact: str, text: str) -> bool:
        """
        发送文本消息。

        纯键盘方案：剪贴板 → Ctrl+V → Enter
        """
        with self._lock, self._automation_session():
            if not self._ready:
                # WeChat may start after the bridge (e.g. after a reboot).
                # Retry discovery lazily instead of requiring a bridge restart.
                self._find_window()
                if not self._window:
                    self._launch_wechat()
                self._ready = self._window is not None
                if not self._ready:
                    log.error("UIA Sender 未就绪")
                    return False

            if not self._ensure_window():
                return False

            # 安全检查：过滤 PIL 引用
            if "<PIL." in text or "PIL." in text:
                log.warning(f"跳过 PIL 引用消息: {text[:60]}")
                return False

            if not self._activate():
                return False
            auto = self._auto

            # 切换到联系人
            if self.search_enabled and contact:
                if not self._switch_contact(contact):
                    return False

            try:
                import pyperclip

                # 模拟真人随机延时
                time.sleep(random.uniform(0.3, 1.0))

                # 复制消息到剪贴板
                pyperclip.copy(text)
                time.sleep(random.uniform(0.1, 0.3))

                # Ctrl+V 粘贴
                self._send_keys('{Ctrl}v', getattr(self, '_input_control', None))

                # 根据消息长度动态等待粘贴完成（长文本多等一会）
                paste_wait = min(len(text) * 0.02, 2.0) + random.uniform(0.3, 0.8)
                time.sleep(paste_wait)

                # Enter 发送
                self._send_keys('{Enter}', getattr(self, '_input_control', None))

                log.info("微信按键操作完成，等待消息回读: %s", contact)
                return True

            except Exception as e:
                log.error(f"[UIA✗] {contact}: {e}")
                return False

    # ================================================================
    # 发送图片 (剪贴板 + 纯键盘)
    # ================================================================

    def send_image(self, contact: str, image_path: str) -> bool:
        """
        发送图片。

        方案：PowerShell 复制图片到剪贴板 → Ctrl+V → Enter
        """
        with self._lock, self._automation_session():
            if not self._ready:
                self._find_window()
                if not self._window:
                    self._launch_wechat()
                self._ready = self._window is not None
                if not self._ready:
                    return False
            if not os.path.isfile(image_path):
                log.error(f"图片不存在: {image_path}")
                return False

            try:
                if not self._ensure_window():
                    return False
                if not self._activate():
                    return False
                auto = self._auto

                if self.search_enabled and contact:
                    if not self._switch_contact(contact):
                        return False

                time.sleep(random.uniform(0.3, 0.8))

                # PowerShell 复制图片到剪贴板
                self._copy_image_to_clipboard(image_path)
                time.sleep(0.3)

                # Ctrl+V 粘贴
                self._send_keys('{Ctrl}v')
                time.sleep(random.uniform(0.8, 1.5))  # 等待微信加载图片预览

                # Enter 发送
                self._send_keys('{Enter}')

                log.info(f"[UIA✓] 图片 → {contact}: {os.path.basename(image_path)}")
                return True

            except Exception as e:
                log.error(f"[UIA✗] 图片 → {contact}: {e}")
                return False

    def _copy_image_to_clipboard(self, path: str):
        """复制图片到剪贴板（通过 PowerShell，避免 PIL 对象被当作文本复制）"""
        abs_path = os.path.abspath(path)
        try:
            subprocess.run([
                "powershell", "-WindowStyle", "Hidden", "-Command",
                f"Add-Type -AssemblyName System.Windows.Forms;"
                f"$img = [System.Drawing.Image]::FromFile('{abs_path}');"
                f"[System.Windows.Forms.Clipboard]::SetImage($img);"
                f"$img.Dispose()"
            ], check=True, timeout=10)
            log.debug("PowerShell 已复制图片到剪贴板")
        except Exception as e:
            log.error(f"复制图片到剪贴板失败: {e}")
            raise
