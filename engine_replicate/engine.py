import importlib
import json
import os
import random
import re
import time
import traceback
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .datatypes import EngineError, InputFile, OutputFile, ProgressEvent

_EMBEDDED_TIMESTAMP_RE = re.compile(r"(?<![0-9])[0-9]{6}_[0-9]{6}(?![0-9])")

# Reference-list slot shapes: even when the endpoint exposes no live schema
# (e.g. minimax/h3), a single URL routed to one of these slots must stay an
# array — providers 422 on a string. Single-image slots (…_image) never match.
_URL_LIST_SLOT_SUFFIXES = ("_urls", "_images", "_videos", "_audios")

_MASKED = "***MASKED***"
_KEY_LIKE_RE = re.compile(r"\br8_[A-Za-z0-9]{4,}\b")
_SECRET_FIELD_RE = re.compile(
    r'("[^"]*(?:api[_-]?key|token|secret|password|authorization)[^"]*"\s*:\s*)"[^"]*"',
    re.IGNORECASE,
)


def _output_stem(stem: str, ts: str) -> str:
    """Name the output file after its source stem and the generation timestamp.

    When the stem already embeds a ``YYMMDD_HHMMSS`` timestamp, the first
    occurrence is replaced in place with ``ts`` — re-running the same bullet
    keeps one date in the name instead of stacking a second one. Stems
    without an embedded timestamp keep the historical ``{ts}-{stem}``
    prepend.
    """
    replaced, count = _EMBEDDED_TIMESTAMP_RE.subn(ts, stem, count=1)
    return replaced if count else f"{ts}-{stem}"


def _mask_key_like(text: str) -> str:
    """Hide API-key-like values so an error log never leaks credentials."""
    text = _KEY_LIKE_RE.sub(_MASKED, text)
    return _SECRET_FIELD_RE.sub(rf'\g<1>"{_MASKED}"', text)


def _json_block(value: Any) -> str:
    """Pretty, secret-masked JSON for the error log (``null`` for None)."""
    if value is None:
        return "null"
    return _mask_key_like(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _display(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _error_metadata(log_path: Path | None) -> dict:
    """Metadata attached to a failed result, naming its error log."""
    return {"error_log": str(log_path)} if log_path else {}


def _media_lines(item: InputFile) -> list[str]:
    """Primary URLs, then every reference slot, one URL per line."""
    lines = ["  primary:"]
    if item.reference_urls:
        lines.extend(f"    {url}" for url in item.reference_urls)
    else:
        lines.append("    (none)")
    for slot, urls in item.references.items():
        lines.append(f"  slot {slot}:")
        if urls:
            lines.extend(f"    {url}" for url in urls)
        else:
            lines.append("    (none)")
    return lines


def _write_error_log(
    *,
    dest_dir: Path,
    base: str,
    idx: int,
    item: InputFile,
    current: int,
    total: int,
    profile: dict,
    platform: str,
    prompt: str,
    expected: Path | None,
    stage: str,
    payload: dict | None = None,
    error: BaseException | None = None,
    error_msg: str = "",
) -> Path | None:
    """Write a verbose ``<base>-<idx>_ERROR.log`` beside the would-be output.

    Called on every per-item failure — provider exception, empty-prompt skip,
    "no output returned", save/download failure — independent of any logging
    flag. Best-effort: when the log itself cannot be written the caller's
    error result still reports the failure, so this never raises.
    """
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{base}-{idx}_ERROR.log"

        duration_raw = item.metadata.get("duration")
        duration_key = str(profile.get("duration_param_name", "duration"))
        duration_effective = (
            payload.get(duration_key, duration_raw) if payload is not None else duration_raw
        )
        fps = item.metadata.get("fps", (profile.get("parameters") or {}).get("fps"))

        lines = [
            f"{path.name} — generation failure",
            f"when: {datetime.now().astimezone().isoformat()}",
            f"item: {current} of {total}",
            f"source: {Path(item.path).resolve()}",
            f"dest dir: {dest_dir.resolve()}",
            f"expected: {expected.resolve() if expected is not None else 'n/a'}",
            f"platform: {platform}",
            f"endpoint: {profile.get('endpoint', '')}",
            "profile:",
            _json_block(profile),
            f"duration: raw={_display(duration_raw)} "
            f"effective={_display(duration_effective)} fps={_display(fps)}",
            "media:",
            *_media_lines(item),
            f"prompt: {prompt if prompt else '(empty)'}",
            f"error stage: {stage}",
            f"error: {error_msg or 'n/a'}",
        ]
        if error is not None:
            lines.append("traceback:")
            lines.append(
                "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ).rstrip()
            )
        else:
            lines.append("traceback: n/a")
        lines += [
            "api payload:",
            _json_block(payload),
            "note: Written automatically on a failed generation — see the "
            "office/MEMORY error-log convention.",
        ]
        path.write_text(_mask_key_like("\n".join(lines)) + "\n", encoding="utf-8")
        return path
    except Exception:
        return None


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

        client = replicate.Client(api_token=self._resolve_api_key(), timeout=600.0)
        endpoint = self._profile["endpoint"]
        params = dict(self._profile.get("parameters", {}))
        media_type = self._profile.get("media_type", "")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        results: list[OutputFile] = []
        total = len(inputs)
        consecutive_conflicts = 0
        circuit_max = int(self._profile.get("circuit_breaker_max", 3))

        for idx, item in enumerate(inputs):
            stem = item.path.stem
            current = idx + 1
            prefix = f"[{current}/{total}]"
            rel_dir = str(item.metadata.get("relative_dir", "") or "")
            dest_dir = self._output_dir / rel_dir if rel_dir else self._output_dir
            ts = datetime.now().strftime("%y%m%d_%H%M%S")
            ext = self._default_extension(media_type)
            base = _output_stem(stem, ts)
            expected = dest_dir / f"{base}-{idx}{ext}"

            delay = float(self._profile.get("delay_between_requests", 0.0))
            if idx > 0 and delay > 0:
                time.sleep(delay)

            self._emit(f"{prefix} 📡 Calling Replicate API...")
            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            failure_context = {
                "dest_dir": dest_dir,
                "base": base,
                "idx": idx,
                "item": item,
                "current": current,
                "total": total,
                "profile": self._profile,
                "platform": self.PLATFORM,
                "prompt": prompt,
                "expected": expected,
            }
            if not prompt:
                empty_prompt_msg = "Empty prompt after applying prefix/suffix"
                log_path = _write_error_log(
                    **failure_context,
                    stage="empty_prompt",
                    error_msg=empty_prompt_msg,
                )
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=empty_prompt_msg,
                    media_type=media_type,
                    expected_path=expected,
                    metadata=_error_metadata(log_path),
                )
                results.append(output)
                continue

            self._emit(f"{prefix} 📝 Markdown: {item.path.name}")

            replicate_input: dict | None = None
            stage = "build_input"
            try:
                replicate_input = self._build_replicate_input(
                    params, prompt, item, media_type
                )
                stage = "provider_call"
                raw_output = self._request_with_retry(
                    client, endpoint, replicate_input, prefix
                )
                self._emit(f"{prefix} ✅ Response received")
                self._emit(f"{prefix} 📦 Payload: {replicate_input}")

                stage = "save"
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
                    no_output_msg = "No output returned from replicate"
                    log_path = _write_error_log(
                        **failure_context,
                        stage="no_output",
                        payload=replicate_input,
                        error_msg=no_output_msg,
                    )
                    output = OutputFile(
                        source_path=item.path,
                        status="error",
                        error_msg=no_output_msg,
                        media_type=media_type,
                        expected_path=expected,
                        metadata=_error_metadata(log_path),
                    )
                else:
                    consecutive_conflicts = 0
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
                self._emit(
                    f"{prefix} Error: {exc}",
                    level="error",
                    current=current,
                    total=total,
                    api_payload=replicate_input,
                )
                log_path = _write_error_log(
                    **failure_context,
                    stage=stage,
                    payload=replicate_input,
                    error=exc,
                    error_msg=f"{type(exc).__name__}: {exc}",
                )
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=str(exc),
                    media_type=media_type,
                    expected_path=expected,
                    metadata=_error_metadata(log_path),
                )
                # A billing/auth failure will reject every remaining item —
                # stop the batch instead of churning through all of them.
                if self._is_fatal(exc):
                    self._emit(
                        f"{prefix} 🛑 Fatal provider error — aborting batch. "
                        f"Check payment, quota, or API key. ({exc})",
                        level="error",
                    )
                    results.append(output)
                    break
                # Transient conflicts exhausted per-item retries. Trip a
                # circuit breaker after N consecutive ones instead of grinding
                # the whole batch since it may fail the same way repeatedly.
                if self._is_rate_limited(exc):
                    consecutive_conflicts += 1
                    if consecutive_conflicts >= circuit_max:
                        self._emit(
                            f"{prefix} 🛑 Circuit breaker tripped after "
                            f"{consecutive_conflicts} consecutive rate-limited "
                            f"failures — aborting batch.",
                            level="error",
                        )
                        results.append(output)
                        break
                else:
                    consecutive_conflicts = 0

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

    def _request_with_retry(
        self, client, endpoint: str, replicate_input: dict, prefix: str
    ) -> Any:
        max_retries = int(self._profile.get("retry_on_429", 4))
        base_delay = float(self._profile.get("retry_base_delay", 2.0))
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return client.run(endpoint, input=replicate_input)
            except Exception as exc:
                rate = self._classify_error(exc)
                if rate != "retryable" or attempt >= max_retries:
                    raise
                last_exc = exc
                # High-demand outages (503 / E003) resolve slower than a plain
                # 429 burst — stretch the delay so we stop hammering the model.
                long_backoff = self._is_high_demand(exc)
                sleep = base_delay * (8 if long_backoff else 2) ** attempt + random.uniform(
                    0, 1
                )
                if long_backoff:
                    label = "high-demand outage"
                elif self._is_timeout(exc):
                    label = "request timeout"
                else:
                    label = "429 rate limit"
                self._emit(
                    f"{prefix} ⏳ {label}, retry {attempt + 1}/{max_retries} in {sleep:.1f}s"
                )
                time.sleep(sleep)
        raise last_exc

    @classmethod
    def _classify_error(cls, exc: Exception) -> str:
        """Classify a provider exception.

        Returns one of ``"retryable"`` (transient load/rate/throttle), ``"fatal"``
        (billing, auth, quota — re-running will not help), or ``"other"``.
        """
        text = str(exc).lower()
        status = getattr(exc, "status", None)
        if status in (402, 403, 401, 409):
            return "fatal"
        if any(
            token in text
            for token in (
                "insufficient",
                "payment",
                "billing",
                "balance",
                "quota",
                "no more",
                "unauthorized",
                "forbidden",
            )
        ):
            return "fatal"
        if status == 429:
            return "retryable"
        if cls._is_high_demand(exc):
            return "retryable"
        if any(
            token in text
            for token in ("ratelimit", "rate limit", "throttl", "too many")
        ):
            return "retryable"
        if cls._is_timeout(exc):
            return "retryable"
        return "other"

    @staticmethod
    def _is_high_demand(exc: Exception) -> bool:
        """Detect Replicate E003 / 503 'service currently unavailable' spikes."""
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "high demand",
                "currently unavailable",
                "e003",
                "503",
                "service unavailable",
            )
        )

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        """Client-side timeouts (cold model starts) — retryable, never fatal."""
        if isinstance(exc, TimeoutError):
            return True
        try:
            import httpx

            return isinstance(exc, httpx.TimeoutException)
        except ImportError:
            return False

    def _is_rate_limited(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "retryable"

    def _is_fatal(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "fatal"

    def _build_replicate_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
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

        Used to shape media params (single URL vs list) and scalar params
        (int vs float vs string) against the model schema. ``$ref``/``allOf``
        fragments are resolved to concrete type dicts. Cached per Engine
        instance; returns {} when the schema is unavailable.
        """
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

    def _resolve_schema_refs(self, node: Any, root: dict) -> Any:
        """Resolve ``$ref``/``allOf`` fragments into a concrete schema dict."""
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

    def _coerce_param_value(self, key: str, value: Any, props: dict) -> Any:
        """Coerce a scalar parameter against the model's schema type.

        Integer-typed params (duration, fps, ...) receive an int when the
        profile ships a numeric string; number/boolean params coerce
        likewise. Non-convertible values (e.g. token durations like
        ``auto``) pass through verbatim, as do all values when the schema
        is unavailable.
        """
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
        """Shape URLs for a media parameter against the model's schema.

        String-typed params (start_image, end_image, image, ...) receive a
        single URL; array-typed params (reference_images, ...) receive the
        full list. When the schema is unknown, a profile-declared
        reference-list slot (``reference_image_urls``, ``reference_images``,
        ...) still receives the full list — a single URL sent as a string is a
        provider 422 (minimax/h3). Other params keep the historical fallback:
        a single URL degrades to a string, multiple URLs stay a list.

        Raises:
            EngineError: multiple URLs routed to a string-typed param — the
                provider can only accept one, so the extra media must never
                be silently dropped.
        """
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
        declared_slots = self._profile.get("slots") or []
        if param_type is None and key in declared_slots and key.endswith(
            _URL_LIST_SLOT_SUFFIXES
        ):
            return urls
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
        named_stem = _output_stem(stem, ts)
        saved = []
        for i, item in enumerate(raw_output):
            if isinstance(item, str):
                self._emit(f"{prefix} ⬇️  Downloading...")
                ext = self._infer_extension(item, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = dest_dir / f"{named_stem}-{idx}{suffix}{ext}"
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
                dest = dest_dir / f"{named_stem}-{idx}{suffix}{ext}"
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
