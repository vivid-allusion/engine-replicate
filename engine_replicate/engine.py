import importlib
import os
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .datatypes import EngineError, InputFile, OutputFile, ProgressEvent


class Engine:
    PLATFORM: str = "replicate"
    PROVIDER_NAME: str = "Replicate"
    PROVIDER_HOMEPAGE: str = "https://replicate.com"
    API_KEY_ENV_VAR: str = "REPLICATE_API_TOKEN"
    API_KEY_PATTERN: str = r"^r8_"

    def __init__(
        self,
        profile: dict,
        output_dir: str | Path,
        api_key: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self._profile = profile
        self._output_dir = Path(output_dir)
        self._api_key = api_key
        self._on_progress = on_progress
        self._prefix = profile.get("prompt_prefix", "")
        self._suffix = profile.get("prompt_suffix", "")
        self._input_props_cache: dict | None = None

    def run(self, inputs: list[InputFile]) -> list[OutputFile]:
        self._validate_preflight()

        import replicate

        client = replicate.Client(api_token=self._resolve_api_key())
        endpoint = self._profile["endpoint"]
        params = dict(self._profile.get("parameters", {}))
        media_type = self._profile.get("media_type", "")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        results: list[OutputFile] = []
        total = len(inputs)

        for idx, item in enumerate(inputs):
            stem = item.path.stem
            current = idx + 1
            prefix = f"[{current}/{total}]"
            rel_dir = str(item.metadata.get("relative_dir", "") or "")
            dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
            ts = datetime.now().strftime("%y%m%d_%H%M%S")
            ext = self._default_extension(media_type)
            expected = dest_dir / f"{ts}-{stem}-{idx}{ext}"

            self._emit(f"{prefix} 📡 Calling Replicate API...")
            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            if not prompt:
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="Empty prompt after applying prefix/suffix",
                    media_type=media_type,
                    expected_path=expected,
                )
                results.append(output)
                continue

            self._emit(f"{prefix} 📝 Prompt: {prompt}")

            try:
                replicate_input = self._build_replicate_input(params, prompt, item, media_type)
                raw_output = client.run(endpoint, input=replicate_input)
                self._emit(f"{prefix} ✅ Response received")
                self._emit(f"{prefix} 📦 Payload: {replicate_input}")

                saved = self._save_results(
                    raw_output,
                    media_type,
                    stem,
                    idx,
                    prefix,
                    current,
                    total,
                    rel_dir,
                    replicate_input,
                )
                if not saved:
                    output = OutputFile(
                        source_path=item.path,
                        status="error",
                        error_msg="No output returned from replicate",
                        media_type=media_type,
                        expected_path=expected,
                    )
                else:
                    for saved_path in saved:
                        results.append(
                            OutputFile(
                                source_path=item.path,
                                path=saved_path,
                                status="ok",
                                media_type=media_type,
                                metadata={"api_payload": replicate_input},
                            )
                        )
                    continue

            except Exception as exc:
                self._emit(f"{prefix} Error: {exc}", level="error", current=current, total=total)
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=str(exc),
                    media_type=media_type,
                    expected_path=expected,
                )

            results.append(output)

        return results

    def _validate_preflight(self):
        if not self._profile.get("endpoint"):
            raise EngineError("Missing or empty 'endpoint' in profile")
        try:
            importlib.import_module("replicate")
        except ImportError:
            raise EngineError("replicate SDK not installed. Run: pip install replicate") from None
        if not self._resolve_api_key():
            raise EngineError(f"{self.API_KEY_ENV_VAR} not set in environment or .env file")

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        return os.environ.get(self.API_KEY_ENV_VAR, "")

    def _default_extension(self, media_type: str) -> str:
        """Profile-driven extension used for precomputed output names."""
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"

    def _build_replicate_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        replicate_input = dict(params)
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
            replicate_input[self._profile.get("duration_param_name", "duration")] = duration

        return replicate_input

    def _reference_key(self) -> str:
        """Primary input key: profile's image_url_param, else reference_param,
        else the provider default image_input."""
        for key in ("image_url_param", "reference_param"):
            value = self._profile.get(key)
            if value:
                return str(value)
        return "image_input"

    def _required_slots(self) -> set[str]:
        return set(self._profile.get("required_slots") or [])

    def _input_props(self) -> dict:
        """Best-effort fetch of the endpoint's Input JSON-schema properties.

        Used to decide whether a media parameter expects a single URL
        (string) or a list of URLs (array). Cached per Engine instance;
        returns {} when the schema is unavailable.
        """
        if self._input_props_cache is not None:
            return self._input_props_cache
        props: dict = {}
        try:
            import replicate

            client = replicate.Client(api_token=self._resolve_api_key())
            model = client.models.get(str(self._profile.get("endpoint", "")))
            schema = getattr(model.latest_version, "openapi_schema", None) or {}
            props = (
                schema.get("components", {})
                .get("schemas", {})
                .get("Input", {})
                .get("properties", {})
            ) or {}
        except Exception:
            props = {}
        if not isinstance(props, dict):
            props = {}
        self._input_props_cache = props
        return props

    def _coerce_media_value(self, key: str, urls: list[str]) -> str | list[str]:
        """Shape URLs for a media parameter against the model's schema.

        String-typed params (start_image, end_image, image, ...) receive a
        single URL; array-typed params (reference_images, ...) receive the
        full list. When the schema is unknown, a single URL degrades to a
        string and multiple URLs stay a list.
        """
        if not urls:
            return []
        param = self._input_props().get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if param_type == "array":
            return urls
        if param_type == "string":
            return urls[0]
        return urls[0] if len(urls) == 1 else urls

    def _save_results(
        self,
        raw_output,
        media_type: str,
        stem: str,
        idx: int,
        prefix: str = "",
        current: int = 0,
        total: int = 0,
        rel_dir: str = "",
        api_payload: dict | None = None,
    ) -> list[Path]:
        if raw_output is None:
            return []
        if not isinstance(raw_output, list):
            raw_output = [raw_output]

        dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%y%m%d_%H%M%S")
        saved = []
        for i, item in enumerate(raw_output):
            if isinstance(item, str):
                self._emit(f"{prefix} ⬇️  Downloading...")
                ext = self._infer_extension(item, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                urllib.request.urlretrieve(item, dest)
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=dest,
                    api_payload=api_payload,
                )
                saved.append(dest)
            elif hasattr(item, "read"):
                self._emit(f"{prefix} ⬇️  Saving file stream...")
                data = item.read()
                ext = self._sniff_extension(data, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{ts}-{stem}-{idx}{suffix}{ext}"
                with open(dest, "wb") as f:
                    f.write(data)
                self._emit(
                    f"{prefix} 💾 Saved: {dest.name}",
                    current=current,
                    total=total,
                    saved_path=dest,
                    api_payload=api_payload,
                )
                saved.append(dest)
        return saved

    def _sniff_extension(self, data: bytes, media_type: str) -> str:
        """Determine the real file extension from the stream's magic bytes.

        Providers often return bytes whose type differs from the requested
        format (e.g. JPEG data for a model run with ``output_format`` set) —
        saving it under a guessed ``.png`` name produces files no viewer
        matches by extension. Falls back to the profile's ``output_format``
        parameter, then to the media-type default.
        """
        for magic, ext in (
            (b"\xff\xd8\xff", ".jpg"),
            (b"\x89PNG\r\n\x1a\n", ".png"),
            (b"GIF87a", ".gif"),
            (b"GIF89a", ".gif"),
        ):
            if data.startswith(magic):
                return ext
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        if data[4:8] == b"ftyp":
            return ".mp4"
        output_format = (
            str(self._profile.get("parameters", {}).get("output_format", "") or "")
            .strip()
            .lstrip(".")
            .lower()
        )
        if output_format and all(c.isalnum() for c in output_format):
            return f".{output_format}"
        return ".mp4" if media_type == "video" else ".png"

    def _infer_extension(self, url: str, media_type: str) -> str:
        if media_type == "video":
            return ".mp4"
        for video_ext in (".mp4", ".mov", ".webm"):
            if video_ext in url.lower():
                return video_ext
        return ".png"

    def _emit(
        self,
        message: str,
        level: str = "info",
        current: int = 0,
        total: int = 0,
        saved_path: Path | None = None,
        api_payload: dict | None = None,
    ):
        if self._on_progress:
            self._on_progress(
                ProgressEvent(
                    message=message,
                    level=level,
                    current=current,
                    total=total,
                    saved_path=saved_path,
                    api_payload=api_payload,
                )
            )
