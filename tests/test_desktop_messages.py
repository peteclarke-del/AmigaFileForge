from __future__ import annotations

import unittest

from desktop.__main__ import _desktop_message_text


class _ScriptValue:
    def __init__(self, value) -> None:
        self.value = value

    def to_string(self):
        return self.value


class _LegacyJavascriptResult:
    def __init__(self, value) -> None:
        self.value = value

    def get_js_value(self):
        return self.value


class DesktopMessageTests(unittest.TestCase):
    def test_current_webkit_value_is_read_directly(self) -> None:
        self.assertEqual(
            "open-images:2",
            _desktop_message_text(_ScriptValue("open-images:2")),
        )

    def test_legacy_webkit_result_wrapper_remains_supported(self) -> None:
        self.assertEqual(
            '{"command":"open-plans","plans":[]}',
            _desktop_message_text(
                _LegacyJavascriptResult(
                    _ScriptValue('{"command":"open-plans","plans":[]}')
                )
            ),
        )

    def test_unsupported_message_object_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "unsupported script message"):
            _desktop_message_text(object())

    def test_non_text_message_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "non-text script message"):
            _desktop_message_text(_ScriptValue(42))


if __name__ == "__main__":
    unittest.main()
