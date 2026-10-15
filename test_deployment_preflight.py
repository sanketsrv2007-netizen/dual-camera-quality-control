"""Offline tests for Step 10 deployment diagnostics and optional GPIO import."""
import ast
import pathlib
import unittest

CODE = pathlib.Path(__file__).with_name("final-code_step11.py")
SOURCE = CODE.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

class DeploymentPreflightTests(unittest.TestCase):
    def test_source_compiles(self):
        compile(SOURCE, str(CODE), "exec")

    def test_gpio_import_is_guarded(self):
        self.assertIn("try:\n    import RPi.GPIO as GPIO\nexcept ImportError:\n    GPIO = None", SOURCE)

    def test_preflight_reports_key_runtime_items(self):
        for item in ("platform.system()", "cv2.__version__", "np.__version__", "v4l2-ctl", "RPi.GPIO"):
            self.assertIn(item, SOURCE)

    def test_preflight_runs_before_camera_discovery(self):
        main = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n.func.id for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("print_runtime_preflight", calls)
        self.assertIn("discover_cameras", calls)
        self.assertLess(calls.index("print_runtime_preflight"), calls.index("discover_cameras"))

    def test_buzzer_fails_clearly_without_gpio(self):
        fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "trigger_buzzer")
        text = ast.get_source_segment(SOURCE, fn)
        self.assertIn("if GPIO is None", text)
        self.assertIn("raise RuntimeError", text)

if __name__ == "__main__":
    unittest.main(verbosity=2)
