"""Tests for scripts/check_schema_sync.py (offline — synthetic schemas).

The live network fetch lives in fetch_input_schema() and is not exercised
here. check_toml()/check_param()/resolve_refs() are pure and fully covered.
"""

import sys
import tomllib
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from check_schema_sync import check_toml, is_media, resolve_refs  # noqa: E402

ALL_CLEAN_PROPS = {
    "prompt": {"type": "string"},
    "image": {"type": "string", "format": "uri"},
    "audio": {"type": "string", "format": "uri"},
    "reference_images": {"type": "array", "items": {"type": "string", "format": "uri"}},
    "duration": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    "fps": {"type": "integer", "enum": [24, 48], "default": 24},
    "mode": {"type": "string", "enum": ["standard", "pro"], "default": "pro"},
    "draft": {"type": "boolean", "default": False},
    "negative_prompt": {"type": "string", "default": ""},
    "guidance": {"type": "number", "minimum": 1.5, "maximum": 10, "default": 4.5},
}

CLEAN_TOML = """\
endpoint = "test/model"

[general]
image_url_param = "image"
slots = ["audio", "reference_images"]

[params.duration]
label = "Duration"
type = "integer"
default = 5
min = 1
max = 20

[params.fps]
label = "FPS"
type = "select"
default = "24"
options = [
  { value = "24", label = "24 FPS" },
  { value = "48", label = "48 FPS" },
]

[params.mode]
label = "Mode"
type = "select"
default = "pro"
options = [
  { value = "pro", label = "Pro" },
  { value = "standard", label = "Standard" },
]

[params.draft]
label = "Draft"
type = "boolean"
default = false

[params.negative_prompt]
label = "Negative Prompt"
type = "text"
default = ""

[params.guidance]
label = "Guidance"
type = "number"
default = 4.5
min = 1.5
max = 10.0
"""


def _toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "model.toml"
    path.write_text(content)
    return path


class TestResolveRefs:
    def test_allof_ref_resolves_type_and_enum(self):
        root = {
            "components": {"schemas": {"fps": {"type": "integer", "enum": [24, 48]}}}
        }
        node = {"allOf": [{"$ref": "#/components/schemas/fps"}], "default": 24}
        assert resolve_refs(node, root) == {
            "type": "integer",
            "enum": [24, 48],
            "default": 24,
        }

    def test_plain_dict_passes_through(self):
        assert resolve_refs({"type": "string"}, {}) == {"type": "string"}


class TestIsMedia:
    def test_uri_string(self):
        assert is_media({"type": "string", "format": "uri"})

    def test_uri_array(self):
        assert is_media({"type": "array", "items": {"type": "string", "format": "uri"}})

    def test_plain_string_not_media(self):
        assert not is_media({"type": "string"})

    def test_int_array_not_media(self):
        assert not is_media({"type": "array", "items": {"type": "integer"}})


class TestCheckToml:
    def test_clean_toml_passes(self, tmp_path):
        assert check_toml(_toml(tmp_path, CLEAN_TOML), ALL_CLEAN_PROPS, ["prompt"]) == []

    def test_undeclared_server_param_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('image_url_param = "image"\n', "")
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert "server param 'image' not declared" in errors[0]

    def test_unknown_toml_param_flagged(self, tmp_path):
        toml = CLEAN_TOML + """
[params.not_a_thing]
label = "Fake"
type = "boolean"
default = false
"""
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert any("'not_a_thing' does not exist" in e for e in errors)

    def test_slot_not_in_schema_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('slots = ["audio", "reference_images"]', 'slots = ["bogus"]')
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert any("slot 'bogus' does not exist" in e for e in errors)

    def test_media_declared_as_param_flagged(self, tmp_path):
        toml = CLEAN_TOML + """
[params.reference_images]
label = "Reference Images"
type = "text"
default = ""
"""
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert any("declare it in [general] slots" in e for e in errors)

    def test_wrong_enum_options_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('{ value = "48", label = "48 FPS" },', "")
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert any("options" in e and "!= server enum" in e for e in errors)

    def test_wrong_integer_range_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace("max = 20", "max = 10")
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert any("server maximum" in e for e in errors)

    def test_wrong_default_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace('default = "pro"', 'default = "standard"')
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt"])
        assert any("server default" in e for e in errors)

    def test_required_param_marked_optional_flagged(self, tmp_path):
        toml = CLEAN_TOML.replace(
            "[params.duration]\nlabel = \"Duration\"\ntype = \"integer\"",
            "[params.duration]\nlabel = \"Duration\"\ntype = \"integer\"\noptional = true",
        )
        errors = check_toml(_toml(tmp_path, toml), ALL_CLEAN_PROPS, ["prompt", "duration"])
        assert any("required server-side but marked optional" in e for e in errors)
