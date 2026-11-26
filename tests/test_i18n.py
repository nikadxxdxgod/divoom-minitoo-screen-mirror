"""Translations: every language covers every string, placeholders match."""

import json
import os
import string
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import i18n_extract  # noqa: E402

from minitoo import i18n  # noqa: E402


def placeholders(s):
    return sorted({f for _t, f, _s, _c in string.Formatter().parse(s) if f})


class TranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = i18n_extract.extract()

    def tearDown(self):
        i18n.set_language("en")

    def test_every_language_has_a_file(self):
        for code in i18n.LANGUAGES:
            if code == "en":
                continue
            self.assertTrue(os.path.exists(os.path.join(i18n.locales_dir(), f"{code}.json")), code)

    def test_complete_and_placeholders(self):
        for code in i18n.LANGUAGES:
            if code == "en":
                continue
            with open(os.path.join(i18n.locales_dir(), f"{code}.json"), encoding="utf-8") as f:
                data = json.load(f)
            missing = [k for k in self.keys if k not in data]
            self.assertEqual(missing, [], f"{code}: missing {missing[:3]}")
            for k in self.keys:
                self.assertEqual(placeholders(k), placeholders(data[k]), f"{code}: {k!r}")
                self.assertTrue(data[k].strip(), f"{code}: empty {k!r}")
            extra = [k for k in data if not k.startswith("@") and k not in self.keys]
            self.assertEqual(extra, [], f"{code}: unused {extra[:3]}")

    def test_switch_and_fallback(self):
        i18n.set_language("ka")
        self.assertEqual(i18n._("Connect"), "დაკავშირება")
        self.assertEqual(i18n._("Display {n}", n=2), "მონიტორი 2")
        self.assertEqual(i18n._("not a known string"), "not a known string")
        self.assertEqual(i18n.set_language("xx"), "en")
        self.assertEqual(i18n._("Connect"), "Connect")

    def test_system_language_is_supported(self):
        self.assertIn(i18n.system_language(), i18n.LANGUAGES)


if __name__ == "__main__":
    unittest.main()
