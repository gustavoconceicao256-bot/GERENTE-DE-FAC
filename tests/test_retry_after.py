from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


class RetryAfterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_retry_after_seconds"
        )
        module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                function,
            ],
            type_ignores=[],
        )
        namespace = {}
        exec(compile(ast.fix_missing_locations(module), "<retry-after-test>", "exec"), namespace)
        cls.parse = staticmethod(namespace["_retry_after_seconds"])

    def test_reads_retry_after_header(self):
        error = SimpleNamespace(
            response=SimpleNamespace(headers={"Retry-After": "2.5"}),
            text="{}",
        )
        self.assertEqual(self.parse(error), 2.5)

    def test_reads_retry_after_body(self):
        error = SimpleNamespace(response=None, text='{"retry_after": 3.25}')
        self.assertEqual(self.parse(error), 3.25)

    def test_never_returns_less_than_one_second(self):
        error = SimpleNamespace(
            response=SimpleNamespace(headers={"Retry-After": "0.1"}),
            text="{}",
        )
        self.assertEqual(self.parse(error), 1.0)

    def test_returns_none_for_invalid_payload(self):
        error = SimpleNamespace(response=None, text="not-json")
        self.assertIsNone(self.parse(error))


if __name__ == "__main__":
    unittest.main()
