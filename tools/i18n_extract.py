"""Collect every translatable string: _("...") and N_("...") calls in minitoo/.

    python tools/i18n_extract.py            print the number of strings
    python tools/i18n_extract.py --template write minitoo/locales/template.json
    python tools/i18n_extract.py --check    report missing/extra keys per language
"""

import ast
import json
import os
import string
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PKG = os.path.join(ROOT, "minitoo")
LOCALES = os.path.join(PKG, "locales")


def extract():
    keys = {}
    for base, _dirs, files in os.walk(PKG):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("_", "N_") and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    rel = os.path.relpath(path, ROOT).replace("\\", "/")
                    keys.setdefault(node.args[0].value, rel)
    return dict(sorted(keys.items(), key=lambda kv: (kv[1], kv[0])))


def placeholders(s):
    return sorted({f for _t, f, _s, _c in string.Formatter().parse(s) if f})


def check(keys):
    ok = True
    for name in sorted(os.listdir(LOCALES)):
        if not name.endswith(".json") or name == "template.json":
            continue
        with open(os.path.join(LOCALES, name), encoding="utf-8") as f:
            data = json.load(f)
        data = {k: v for k, v in data.items() if not k.startswith("@")}
        missing = [k for k in keys if k not in data]
        extra = [k for k in data if k not in keys]
        bad = [k for k in keys if k in data and placeholders(k) != placeholders(data[k])]
        if missing or extra or bad:
            ok = False
        print(f"{name}: {len(data)} entries, missing {len(missing)}, extra {len(extra)}, "
              f"bad placeholders {len(bad)}")
        for k in (missing + extra + bad)[:10]:
            print("   ", repr(k))
    return ok


if __name__ == "__main__":
    keys = extract()
    if "--template" in sys.argv:
        with open(os.path.join(LOCALES, "template.json"), "w", encoding="utf-8") as f:
            json.dump({k: "" for k in keys}, f, ensure_ascii=False, indent=1)
        print("template.json:", len(keys), "strings")
    elif "--check" in sys.argv:
        sys.exit(0 if check(keys) else 1)
    else:
        print(len(keys), "strings")
        for k, src in keys.items():
            print(f"{src}\t{k}")
