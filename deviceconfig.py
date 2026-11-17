"""Patch a CANedge2 configuration file with the settings this team needs.

Why this is a patcher and not a generator: the config is a JSON file whose
layout is defined by CSS Electronics' per-firmware *Rule Schema*, and that
schema isn't available until the device (or its docs download) is.  Rather than
guess key names, the desired settings live in config/canedge2_settings.yaml as
(json path -> value) pairs.  On bring-up day:

  1. Open the stock config in CSS's config editor, set the options once, save.
  2. Read the saved JSON, copy each option's path into the YAML (replace TODO).
  3. `python -m canedge2.deviceconfig --base stock.json --out config-XX.YY.json`
     re-applies the settings to any future base config (new firmware, 2nd unit)
     and validates against the schema if you pass --schema.

Refuses to run while any path is still TODO.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def set_path(doc: dict, path: str, value) -> None:
    keys = path.split(".")
    cur = doc
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            raise KeyError(f"path '{path}': '{k}' not found in base config")
        cur = cur[k]
    if keys[-1] not in cur:
        raise KeyError(f"path '{path}': '{keys[-1]}' not found in base config")
    cur[keys[-1]] = value


def apply_settings(base: dict, settings: dict) -> dict:
    todo = [k for k, v in settings.items() if str(v.get("path", "TODO")).startswith("TODO")]
    if todo:
        raise ValueError("fill in the JSON path for: " + ", ".join(todo))
    out = json.loads(json.dumps(base))     # deep copy
    for name, spec in settings.items():
        set_path(out, spec["path"], spec["value"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", required=True, help="config JSON saved from CSS's editor")
    ap.add_argument("--settings", default=str(Path(__file__).parent.parent / "config" / "canedge2_settings.yaml"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--schema", help="Rule Schema JSON for the same firmware (optional; needs jsonschema)")
    a = ap.parse_args(argv)

    base = json.loads(Path(a.base).read_text())
    settings = yaml.safe_load(Path(a.settings).read_text())["settings"]
    out = apply_settings(base, settings)

    if a.schema:
        import jsonschema
        jsonschema.validate(out, json.loads(Path(a.schema).read_text()))
        print("schema validation: OK")
    Path(a.out).write_text(json.dumps(out, indent=2))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
