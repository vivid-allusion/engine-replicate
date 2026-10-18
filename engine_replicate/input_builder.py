"""Payload building — the input-builder half of the Engine, split out.

Mixin methods on Engine; names and behavior are unchanged from the
pre-split gold engine (`check_schema_sync` and the generators keep using
them through inheritance).
"""

import importlib

from .datatypes import EngineError


class InputBuilder:
    def _build_replicate_input(
        self, params: dict, prompt: str, item, media_type: str
    ) -> dict:
        props = self._input_props()
        replicate_input = {k: self._coerce_param_value(k, v, props) for k, v in params.items()}
        replicate_input.pop("prompt_prefix", None)
        replicate_input.pop("prompt_suffix", None)

        replicate_input["prompt"] = prompt
        ref_key = self._reference_key()
        if item.reference_urls:
            replicate_input[ref_key] = self._coerce_media_value(ref_key, item.reference_urls)
        for slot, urls in item.references.items():
            if urls or slot in self._required_slots():
                replicate_input[slot] = self._coerce_media_value(slot, urls)

        duration = item.metadata.get("duration")
        if duration is not None:
            key = str(self._profile.get("duration_param_name", "duration"))
            replicate_input[key] = self._coerce_param_value(key, duration, props)

        return replicate_input

    def _reference_key(self) -> str:
        for key in ("image_url_param", "reference_param"):
            value = self._profile.get(key)
            if value:
                return str(value)
        return "image_input"

    def _required_slots(self) -> set[str]:
        return set(self._profile.get("required_slots") or [])

    def _input_props(self) -> dict:
        if self._input_props_cache is not None:
            return self._input_props_cache
        props: dict = {}
        try:
            import replicate

            client = replicate.Client(api_token=self._resolve_api_key())
            model = client.models.get(str(self._profile.get("endpoint", "")))
            schema = getattr(model.latest_version, "openapi_schema", None) or {}
            raw = (
                schema.get("components", {})
                .get("schemas", {})
                .get("Input", {})
                .get("properties", {})
            ) or {}
            props = {
                key: self._resolve_schema_refs(value, schema)
                for key, value in raw.items()
                if isinstance(value, dict)
            }
        except Exception:
            props = {}
        if not isinstance(props, dict):
            props = {}
        self._input_props_cache = props
        return props

    def _resolve_schema_refs(self, node, root: dict):
        if not isinstance(node, dict):
            return node
        resolved: dict = {}
        if "$ref" in node and isinstance(node["$ref"], str):
            target: Any = root
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target.get(part) if isinstance(target, dict) else None
                if target is None:
                    break
            if isinstance(target, dict):
                resolved.update(self._resolve_schema_refs(target, root))
        resolved.update({k: v for k, v in node.items() if k not in ("$ref", "allOf")})
        for fragment in node.get("allOf", []):
            merged = self._resolve_schema_refs(fragment, root)
            if isinstance(merged, dict):
                for k, v in merged.items():
                    resolved.setdefault(k, v)
        return resolved

    def _coerce_param_value(self, key: str, value, props: dict):
        param = props.get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if not isinstance(value, str):
            return value
        if param_type == "integer":
            try:
                return int(value)
            except ValueError:
                return value
        if param_type == "number":
            try:
                return float(value)
            except ValueError:
                return value
        if param_type == "boolean":
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1", "on"}:
                return True
            if lowered in {"false", "no", "0", "off"}:
                return False
        return value

    def _coerce_media_value(self, key: str, urls: list[str]) -> str | list[str]:
        if not urls:
            return []
        param = self._input_props().get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if param_type == "array":
            return urls
        if param_type == "string":
            if len(urls) > 1:
                raise EngineError(
                    f"Param '{key}' accepts a single media URL but {len(urls)} "
                    f"were provided — fix the markdown: {urls}"
                )
            return urls[0]
        return urls[0] if len(urls) == 1 else urls
