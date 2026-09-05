#!/usr/bin/env python3
"""Endpoint TOML ↔ live schema sync checker.

THE ENDPOINT TOML CATALOG IS THE SINGLE SOURCE OF TRUTH FOR A MODEL'S
INPUTS. Every input the model accepts server-side MUST be declared in the
TOML, and every TOML declaration MUST match the live openapi schema.

Run:

    REPLICATE_API_TOKEN=r8_... python scripts/check_schema_sync.py

Exit code 1 when any endpoint TOML drifts from its live schema.

Coverage rules (per endpoint):
- Every server-side Input property except ``prompt`` must be declared as
  the ``image_url_param``, a ``[general] slots`` entry (media/uri inputs),
  or a ``[params.*]`` entry.
- Every declared slot must exist server-side as a uri-typed (string or
  array-of-string) input. Media inputs must NEVER be declared as
  ``[params.*]``.
- ``[params.*]`` must mirror the server schema: ``select`` options exactly
  equal the server enum, ``integer``/``number`` min/max equal the server
  range, and defaults equal the server default (when the server declares
  one).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ENDPOINTS_DIR = REPO_ROOT / "engine_replicate" / "endpoints"


def resolve_refs(node: Any, root: dict, seen: set[str] | None = None) -> Any:
    """Resolve ``$ref``/``allOf`` fragments into a concrete schema dict."""
    if not isinstance(node, dict):
        return node
    if seen is None:
        seen = set()
    out: dict = {}
    if isinstance(node.get("$ref"), str):
        ref = node["$ref"]
        if ref not in seen:
            target: Any = root
            for part in ref.lstrip("#/").split("/"):
                target = target.get(part) if isinstance(target, dict) else None
                if target is None:
                    break
            if isinstance(target, dict):
                out.update(resolve_refs(target, root, seen | {ref}))
    out.update({k: v for k, v in node.items() if k not in ("$ref", "allOf")})
    for fragment in node.get("allOf", []):
        merged = resolve_refs(fragment, root, seen)
        if isinstance(merged, dict):
            for k, v in merged.items():
                out.setdefault(k, v)
    return out


def fetch_input_schema(endpoint: str, api_key: str) -> tuple[dict, list[str], str]:
    """Fetch (resolved props, required list, error message)."""
    try:
        import replicate

        client = replicate.Client(api_token=api_key)
        model = client.models.get(endpoint)
        schema = getattr(model.latest_version, "openapi_schema", None) or {}
        input_schema = schema.get("components", {}).get("schemas", {}).get("Input", {})
        raw = input_schema.get("properties", {}) or {}
        props = {k: resolve_refs(v, schema) for k, v in raw.items() if isinstance(v, dict)}
        required = list(input_schema.get("required", []) or [])
        return props, required, ""
    except Exception as exc:  # noqa: BLE001
        return {}, [], f"{type(exc).__name__}: {exc}"


def is_media(spec: dict) -> bool:
    """True for uri-typed string inputs and arrays of uri-typed strings."""
    if spec.get("type") == "string" and spec.get("format") == "uri":
        return True
    if spec.get("type") == "array":
        items = spec.get("items")
        if isinstance(items, dict) and items.get("format") == "uri":
            return True
    return False


def check_param(key: str, toml_param: dict, spec: dict, errors: list[str]) -> None:
    """Verify a single [params.*] entry against its server schema."""
    if is_media(spec):
        errors.append(
            f"param '{key}' is a media (uri) input — declare it in [general] slots instead"
        )
        return
    ptype = spec.get("type")
    penum = spec.get("enum")
    ttype = toml_param.get("type")
    pdefault = spec.get("default")

    if ptype == "string" and penum:
        _check_select(key, toml_param, penum, pdefault, errors)
    elif ptype == "integer" and penum:
        _check_select(key, toml_param, penum, pdefault, errors)
    elif ptype == "integer":
        if ttype != "integer":
            errors.append(f"param '{key}': server type integer, TOML type '{ttype}'")
            return
        _check_range(key, toml_param, spec, pdefault, errors)
    elif ptype == "number":
        if ttype != "number":
            errors.append(f"param '{key}': server type number, TOML type '{ttype}'")
            return
        _check_range(key, toml_param, spec, pdefault, errors)
    elif ptype == "boolean":
        if ttype != "boolean":
            errors.append(f"param '{key}': server type boolean, TOML type '{ttype}'")
        elif pdefault is not None and toml_param.get("default") != pdefault:
            errors.append(
                f"param '{key}': default {toml_param.get('default')!r} != server default {pdefault!r}"
            )
    elif ptype == "string":
        if ttype not in ("text", "string"):
            errors.append(f"param '{key}': server type string, TOML type '{ttype}'")
        elif pdefault is not None and toml_param.get("default") != pdefault:
            errors.append(
                f"param '{key}': default {toml_param.get('default')!r} != server default {pdefault!r}"
            )


def _check_select(
    key: str, toml_param: dict, penum: list[Any], pdefault: Any, errors: list[str]
) -> None:
    if toml_param.get("type") != "select":
        errors.append(f"param '{key}': server enum, TOML type '{toml_param.get('type')}'")
        return
    toml_values = [str(o.get("value")) for o in toml_param.get("options", [])]
    if len(toml_values) != len(set(toml_values)):
        errors.append(f"param '{key}': duplicate option values in {toml_values}")
    server_values = [str(v) for v in penum]
    if set(toml_values) != set(server_values):
        errors.append(f"param '{key}': options {toml_values} != server enum {server_values}")
    if pdefault is not None and str(toml_param.get("default")) != str(pdefault):
        errors.append(
            f"param '{key}': default {toml_param.get('default')!r} != server default {pdefault!r}"
        )


def _check_range(
    key: str, toml_param: dict, spec: dict, pdefault: Any, errors: list[str]
) -> None:
    if spec.get("minimum") is not None and toml_param.get("min") != spec["minimum"]:
        errors.append(
            f"param '{key}': min {toml_param.get('min')!r} != server minimum {spec['minimum']!r}"
        )
    if spec.get("maximum") is not None and toml_param.get("max") != spec["maximum"]:
        errors.append(
            f"param '{key}': max {toml_param.get('max')!r} != server maximum {spec['maximum']!r}"
        )
    if pdefault is not None and toml_param.get("default") != pdefault:
        errors.append(
            f"param '{key}': default {toml_param.get('default')!r} != server default {pdefault!r}"
        )


def check_toml(toml_path: Path, props: dict, required: list[str]) -> list[str]:
    """Verify one endpoint TOML against its live schema. Returns violations."""
    errors: list[str] = []
    data = tomllib.loads(toml_path.read_text())
    general = data.get("general", {})
    primary = general.get("image_url_param", "")
    slots = list(general.get("slots") or [])
    params = data.get("params", {})

    for key, spec in props.items():
        if key == "prompt" or key == primary or key in slots or key in params:
            continue
        errors.append(f"server param '{key}' not declared (not primary/slot/param)")

    if primary:
        if primary not in props:
            errors.append(f"image_url_param '{primary}' does not exist server-side")
        elif not is_media(props[primary]):
            errors.append(f"image_url_param '{primary}' is not a media (uri) input")

    for slot in slots:
        if slot not in props:
            errors.append(f"slot '{slot}' does not exist server-side")
        elif not is_media(props[slot]):
            errors.append(f"slot '{slot}' is not a media (uri) input")

    for key, toml_param in params.items():
        if key not in props:
            errors.append(f"param '{key}' does not exist server-side")
            continue
        check_param(key, toml_param, props[key], errors)
        if key in required and toml_param.get("optional"):
            errors.append(f"param '{key}' is required server-side but marked optional")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint", help="check only this endpoint (e.g. prunaai/p-video)"
    )
    parser.add_argument(
        "--api-key", help=f"Replicate API token (default: $REPLICATE_API_TOKEN)"
    )
    args = parser.parse_args()

    import os

    api_key = args.api_key or os.environ.get("REPLICATE_API_TOKEN", "")
    if not api_key:
        print("error: REPLICATE_API_TOKEN is required (env or --api-key)")
        return 2

    tomls = sorted(ENDPOINTS_DIR.rglob("*.toml"))
    failures = 0
    checked = 0
    for toml_path in tomls:
        data = tomllib.loads(toml_path.read_text())
        endpoint = data.get("endpoint", "")
        if args.endpoint and endpoint != args.endpoint:
            continue
        checked += 1
        props, required, error = fetch_input_schema(endpoint, api_key)
        if error:
            print(f"FAIL {toml_path.name} ({endpoint}): schema fetch failed — {error}")
            failures += 1
            continue
        violations = check_toml(toml_path, props, required)
        if violations:
            failures += 1
            print(f"FAIL {toml_path.name} ({endpoint}):")
            for v in violations:
                print(f"    - {v}")
        else:
            print(f"OK   {toml_path.name} ({endpoint}) — {len(props)} server inputs")

    print(f"\n{checked} endpoint(s) checked, {failures} drifted")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
