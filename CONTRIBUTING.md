# Contributing

Thanks for helping! Bug reports with a `diagnostic_report.txt` (from `MiniTooDiagnose.exe` or `diagnose.bat`) are especially useful.

## Development setup

```bat
install.bat          :: .venv + packages
start.bat --simulator
run_tests.bat
```

The simulator (`minitoo/simulator.py`) behaves like the real device, so most work needs no hardware.

## Translations

Every user-visible string is written in English in the code as `_("…")`, or as `N_("…")` for tables that are translated later. Each language is one JSON file in `minitoo/locales/` that maps the English text to the translation.

**Fix a translation:** edit the value in `minitoo/locales/<code>.json`.

**Add a language:**

1. Copy `minitoo/locales/template.json` to `minitoo/locales/<code>.json` and fill in the values. Keep placeholders like `{n}` exactly as they are.
2. Add the code to `LANGUAGES` in `minitoo/i18n.py`.
3. Run the tests. They fail if a string or a placeholder is missing.

```bat
.venv\Scripts\python tools\i18n_extract.py --check
.venv\Scripts\python -m unittest tests.test_i18n
```

**After changing UI text in the code**, run `tools\i18n_extract.py --template` to refresh `template.json`. Then add the new strings to every language file.

## Safety rules for code changes

- Only allow-listed commands may be sent to the device: `protocol.ALLOWED_COMMANDS`, which holds `0x8B` and `0x74`. Do not add commands that are not documented and tested on a real MiniToo.
- Some opcodes (`0x40`, `0xA3`, `0xA4`, `0xAD`, `0xAE`, `0x3C`) are known to freeze or crash the device.
- Keep uploads under `protocol.MAX_SAFE_PAYLOAD`.
