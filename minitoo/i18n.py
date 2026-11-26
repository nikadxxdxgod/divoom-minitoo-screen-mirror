"""Tiny translation layer.

English text is the key: ``_("Connect")``. Each language is a JSON file in
``minitoo/locales/<code>.json`` that maps the English text to its translation.
Missing entries fall back to English, so a partial translation never breaks
the program. Placeholders use ``str.format`` names: ``_("{n} frames", n=3)``.

To add a language: copy ``locales/de.json``, translate the values, add the
code to ``LANGUAGES`` below and run the tests (they check every key and
placeholder).
"""

from __future__ import annotations

import json
import locale
import logging
import os
import sys
from typing import Dict, Optional

log = logging.getLogger("minitoo.i18n")

# code -> (native name, English name)
LANGUAGES: Dict[str, tuple] = {
    "en": ("English", "English"),
    "ka": ("ქართული", "Georgian"),
    "de": ("Deutsch", "German"),
    "fr": ("Français", "French"),
    "es": ("Español", "Spanish"),
    "it": ("Italiano", "Italian"),
    "pt": ("Português (Brasil)", "Portuguese (Brazil)"),
    "pl": ("Polski", "Polish"),
    "tr": ("Türkçe", "Turkish"),
    "uk": ("Українська", "Ukrainian"),
    "ru": ("Русский", "Russian"),
    "zh": ("简体中文", "Chinese (Simplified)"),
    "ja": ("日本語", "Japanese"),
    "ko": ("한국어", "Korean"),
}
DEFAULT_LANGUAGE = "en"

# Inno Setup installer language name -> our code (written by the installer)
INSTALLER_LANGUAGES = {
    "english": "en", "georgian": "ka", "german": "de", "french": "fr", "spanish": "es",
    "italian": "it", "brazilianportuguese": "pt", "polish": "pl", "turkish": "tr",
    "ukrainian": "uk", "russian": "ru", "chinesesimplified": "zh", "japanese": "ja",
    "korean": "ko",
}

_current = DEFAULT_LANGUAGE
_table: Dict[str, str] = {}


def locales_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")


def load_table(code: str) -> Dict[str, str]:
    if code == DEFAULT_LANGUAGE:
        return {}
    path = os.path.join(locales_dir(), f"{code}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning("Translation %s could not be loaded: %s", code, e)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v and not k.startswith("@")}


def set_language(code: Optional[str]) -> str:
    global _current, _table
    code = code if code in LANGUAGES else DEFAULT_LANGUAGE
    _table = load_table(code)
    _current = code
    return code


def current_language() -> str:
    return _current


def _(text: str, **kwargs) -> str:
    s = _table.get(text, text)
    if kwargs:
        try:
            return s.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text.format(**kwargs)
    return s


def N_(text: str) -> str:
    """Marks a string for translation without translating it yet (for tables
    that are defined at import time and translated later with ``_()``)."""
    return text


def system_language() -> str:
    """Best guess from Windows' UI language (falls back to English)."""
    tag = ""
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            tag = locale.windows_locale.get(lcid, "")
        except Exception:
            tag = ""
    if not tag:
        try:
            tag = locale.getlocale()[0] or ""
        except Exception:
            tag = ""
    code = tag.split("_")[0].split("-")[0].lower()
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def installer_language() -> Optional[str]:
    """Program language chosen in the Windows installer
    (HKCU\\Software\\MiniTooMirror, value "Language" = our code)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\MiniTooMirror") as k:
            value, _type = winreg.QueryValueEx(k, "Language")
    except OSError:
        return None
    value = str(value).strip().lower()
    if value in LANGUAGES:
        return value
    return INSTALLER_LANGUAGES.get(value)
