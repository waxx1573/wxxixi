import ast
import asyncio
from pathlib import Path
import types
import unittest


def load_method(path, class_name, method_name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name)
    method.decorator_list = []
    method.returns = None
    for arg in method.args.args:
        arg.annotation = None
    namespace = {"unwrap_event": lambda event: getattr(event, "event", event)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[method_name]


class AdminBoundaryTests(unittest.TestCase):
    def test_group_manager_uses_native_role_only(self):
        path = Path(__file__).resolve().parents[1] / "astrbot_plugin_wechat_group_manager/main.py"
        check = load_method(path, "Main", "_is_admin")
        owner = types.SimpleNamespace(config={"administrator_ids": ["member"], "bot_self_ids": ["member"]})
        self.assertFalse(check(owner, types.SimpleNamespace(is_admin=lambda: False), "group"))
        self.assertTrue(check(owner, types.SimpleNamespace(is_admin=lambda: True), "group"))
        self.assertFalse(check(owner, types.SimpleNamespace(), "group"))
        self.assertFalse(check(owner, types.SimpleNamespace(is_admin=lambda: "admin"), "group"))

    def test_group_manager_fails_closed_on_identity_error(self):
        path = Path(__file__).resolve().parents[1] / "astrbot_plugin_wechat_group_manager/main.py"
        check = load_method(path, "Main", "_is_admin")
        def broken():
            raise RuntimeError("identity unavailable")
        self.assertFalse(check(object(), types.SimpleNamespace(is_admin=broken), "group"))


if __name__ == "__main__":
    unittest.main()
