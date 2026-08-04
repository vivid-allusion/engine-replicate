import os
import urllib.request
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
        on_progress: "Callable[[str], None] | None" = None,
    ):
        self._profile = profile
        self._output_dir = Path(output_dir)
        self._api_key = api_key
        self._on_progress = on_progress
        self._prefix = profile.get("prompt_prefix", "")
        self._suffix = profile.get("prompt_suffix", "")

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
            self._emit(f"Sending Markdown files to AI model ({idx + 1}/{total})...")
            prompt = f"{self._prefix}{item.prompt}{self._suffix}".strip()
            if not prompt:
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg="Empty prompt after applying prefix/suffix",
                    media_type=media_type,
                )
                results.append(output)
                continue

            try:
                replicate_input = self._build_replicate_input(
                    params, prompt, item, media_type
                )
                raw_output = client.run(endpoint, input=replicate_input)

                saved = self._save_results(raw_output, media_type, item.path.stem, idx)
                if not saved:
                    output = OutputFile(
                        source_path=item.path,
                        status="error",
                        error_msg="No output returned from replicate",
                        media_type=media_type,
                    )
                else:
                    for saved_path in saved:
                        results.append(
                            OutputFile(
                                source_path=item.path,
                                path=saved_path,
                                status="ok",
                                media_type=media_type,
                            )
                        )
                    continue

            except Exception as exc:
                output = OutputFile(
                    source_path=item.path,
                    status="error",
                    error_msg=str(exc),
                    media_type=media_type,
                )

            results.append(output)

        return results

    def _validate_preflight(self):
        if "endpoint" not in self._profile:
            raise EngineError("Missing 'endpoint' in profile")
        try:
            import replicate
        except ImportError:
            raise EngineError(
                "replicate SDK not installed. Run: pip install replicate"
            ) from None
        if not self._resolve_api_key():
            raise EngineError(
                f"{self.API_KEY_ENV_VAR} not set in environment or .env file"
            )

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        return os.environ.get(self.API_KEY_ENV_VAR, "")

    def _build_replicate_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        replicate_input = dict(params)
        replicate_input.pop("prompt_prefix", None)
        replicate_input.pop("prompt_suffix", None)

        if media_type == "image":
            replicate_input["prompt"] = prompt
            if item.reference_urls:
                replicate_input["image"] = item.reference_urls[0]
        elif media_type == "video":
            replicate_input["prompt"] = prompt
            if item.reference_urls:
                replicate_input["start_image"] = item.reference_urls[0]
            for key in ("duration", "fps"):
                if key in item.metadata:
                    replicate_input[key] = item.metadata[key]
        else:
            replicate_input["prompt"] = prompt

        return replicate_input

    def _save_results(self, raw_output, media_type: str, stem: str, idx: int) -> list[Path]:
        if raw_output is None:
            return []
        if not isinstance(raw_output, list):
            raw_output = [raw_output]

        ts = datetime.now().strftime("%y%m%d_%H%M%S")
        saved = []
        for i, item in enumerate(raw_output):
            if isinstance(item, str):
                ext = self._infer_extension(item, media_type)
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = self._output_dir / f"{stem}-{idx}-{ts}{suffix}{ext}"
                urllib.request.urlretrieve(item, dest)
                saved.append(dest)
            elif hasattr(item, "read"):
                ext = ".mp4" if media_type == "video" else ".png"
                suffix = "" if len(raw_output) == 1 else f"_{i}"
                dest = self._output_dir / f"{stem}-{idx}-{ts}{suffix}{ext}"
                with open(dest, "wb") as f:
                    f.write(item.read())
                saved.append(dest)
        return saved

    def _infer_extension(self, url: str, media_type: str) -> str:
        if media_type == "video":
            return ".mp4"
        for video_ext in (".mp4", ".mov", ".webm"):
            if video_ext in url.lower():
                return video_ext
        return ".png"

    def _emit(self, message: str, level: str = "info"):
        if self._on_progress:
            self._on_progress(message)
