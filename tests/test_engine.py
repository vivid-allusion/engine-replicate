import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine_replicate.metadata as meta  # noqa: E402
from engine_replicate import Engine, EngineError, InputFile, OutputFile, ProgressEvent  # noqa: E402


class TestDatatypes:
    def test_inputfile_defaults(self):
        f = InputFile(path=Path("test.md"), prompt="hello")
        assert f.path == Path("test.md")
        assert f.prompt == "hello"
        assert f.reference_urls == []
        assert f.references == {}
        assert f.metadata == {}

    def test_outputfile_defaults(self):
        o = OutputFile(source_path=Path("test.md"))
        assert o.source_path == Path("test.md")
        assert o.path is None
        assert o.status == "ok"
        assert o.error_msg == ""
        assert o.media_type == ""
        assert o.metadata == {}
        assert o.expected_path is None

    def test_outputfile_error_status(self):
        o = OutputFile(
            source_path=Path("test.md"),
            status="error",
            error_msg="timeout",
            media_type="image",
        )
        assert o.status == "error"
        assert o.error_msg == "timeout"
        assert o.media_type == "image"

    def test_progress_event_defaults(self):
        e = ProgressEvent(message="processing")
        assert e.message == "processing"
        assert e.level == "info"

    def test_engine_error_is_exception(self):
        with pytest.raises(EngineError):
            raise EngineError("test")


class TestMetadata:
    def test_provider_name(self):
        assert meta.PROVIDER_NAME == "Replicate"

    def test_platform(self):
        assert meta.PLATFORM == "replicate"

    def test_api_key_env_var(self):
        assert meta.API_KEY_ENV_VAR == "REPLICATE_API_TOKEN"

    def test_api_key_pattern(self):
        assert meta.API_KEY_PATTERN == r"^r8_"

    def test_provider_homepage(self):
        assert meta.PROVIDER_HOMEPAGE == "https://replicate.com"

    def test_metadata_matches_engine_class(self):
        assert Engine.PLATFORM == meta.PLATFORM
        assert Engine.PROVIDER_NAME == meta.PROVIDER_NAME
        assert Engine.API_KEY_ENV_VAR == meta.API_KEY_ENV_VAR
        assert Engine.API_KEY_PATTERN == meta.API_KEY_PATTERN


class TestEngineInitPurity:
    def test_init_stores_attributes(self):
        profile = {"endpoint": "test/model", "media_type": "image"}
        engine = Engine(profile, "/tmp/out")
        assert engine._profile == profile
        assert engine._output_dir == Path("/tmp/out")
        assert engine._api_key is None
        assert engine._on_progress is None
        assert engine._prefix == ""
        assert engine._suffix == ""

    def test_init_extracts_prefix_suffix(self):
        profile = {
            "endpoint": "test/model",
            "prompt_prefix": "Turn this into ",
            "prompt_suffix": " in oil painting style",
        }
        engine = Engine(profile, "/tmp/out")
        assert engine._prefix == "Turn this into "
        assert engine._suffix == " in oil painting style"


class TestEnginePreflight:
    def test_missing_endpoint_raises(self):
        engine = Engine({"media_type": "image"}, "/tmp/out")
        with pytest.raises(EngineError, match="Missing or empty 'endpoint'"):
            engine.run([])

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_raises(self):
        with patch.dict("sys.modules", {"replicate": MagicMock()}):
            engine = Engine({"endpoint": "test/model"}, "/tmp/out")
            with pytest.raises(EngineError, match="not set"):
                engine.run([])

    def test_replicate_not_installed_raises(self):
        with patch.dict("sys.modules", {"replicate": None}):
            engine = Engine({"endpoint": "test/model"}, "/tmp/out")
            with pytest.raises(EngineError, match="replicate SDK not installed"):
                engine.run([])


class TestEngineRun:
    def test_empty_inputs(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                results = engine.run([])
                assert results == []

    def test_run_calls_progress_callback(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = ["https://example.com/out.png"]
            mock_replicate.Client.return_value = mock_client

            progress_calls = []
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="test")])

            assert len(progress_calls) >= 1
            assert any("Calling Replicate API" in c.message for c in progress_calls)

    def test_applies_prefix_suffix(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = []
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "prompt_prefix": "PREFIX: ",
                        "prompt_suffix": " :SUFFIX",
                    },
                    tmp_path,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="hello")])
                call_args = mock_client.run.call_args
                prompt_sent = call_args[1]["input"]["prompt"]
                assert prompt_sent == "PREFIX: hello :SUFFIX"

    def test_empty_prompt_after_prefix_suffix_is_error(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "prompt_prefix": "",
                        "prompt_suffix": "",
                    },
                    tmp_path,
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="  ")])
                assert len(results) == 1
                assert results[0].status == "error"
                assert "Empty prompt" in results[0].error_msg

    def test_per_markdown_file_error_returns_error_outputfile(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = RuntimeError("API timeout")
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="test")])
                assert len(results) == 1
                assert results[0].status == "error"
                assert "API timeout" in results[0].error_msg

    def test_partial_success_mixed_batch(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = [
                ["https://a.com/ok.png"],
                RuntimeError("fail"),
                ["https://a.com/ok2.png"],
            ]
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    markdown_files = [InputFile(path=Path(f"b{i}.md"), prompt="test") for i in range(3)]
                    results = engine.run(markdown_files)
                    statuses = [r.status for r in results]
                    assert statuses.count("ok") == 2
                    assert statuses.count("error") == 1

    def test_save_results_downloads_urls(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = ["https://example.com/img.png"]
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.return_value = (None, None)
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    results = engine.run([InputFile(path=Path("b.md"), prompt="test")])
                    assert len(results) == 1
                    assert results[0].status == "ok"
                    assert results[0].path is not None
                    mock_retrieve.assert_called_once()

    def test_save_results_mirrors_relative_dir(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = ["https://example.com/img.png"]
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("urllib.request.urlretrieve") as mock_retrieve:
                    mock_retrieve.return_value = (None, None)
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    results = engine.run(
                        [
                            InputFile(
                                path=Path("b.md"),
                                prompt="test",
                                metadata={"relative_dir": "scene1/nested"},
                            )
                        ]
                    )
                    assert len(results) == 1
                    assert results[0].status == "ok"
                    assert results[0].path is not None
                    assert results[0].path.parent == tmp_path / "scene1" / "nested"

    def test_none_output_returns_error(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = None
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                results = engine.run([InputFile(path=Path("b.md"), prompt="test")])
                assert len(results) == 1
                assert results[0].status == "error"
                assert "No output" in results[0].error_msg

    def test_video_media_type_uses_start_image(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = ["https://example.com/out.mp4"]
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "test/model",
                            "media_type": "video",
                            "reference_param": "start_image",
                            "parameters": {"duration": 5, "fps": 24},
                        },
                        tmp_path,
                    )
                    engine.run(
                        [
                            InputFile(
                                path=Path("b.md"),
                                prompt="test",
                                reference_urls=["https://ref.com/img.jpg"],
                            )
                        ]
                    )
                    replicate_input = mock_client.run.call_args[1]["input"]
                    assert replicate_input["start_image"] == "https://ref.com/img.jpg"
                    assert replicate_input["duration"] == 5
                    assert replicate_input["fps"] == 24


class TestStreamSaveExtension:
    """Stream outputs must be saved under the extension their bytes claim."""

    def _run_stream(self, tmp_path, data: bytes, profile: dict | None = None):
        from io import BytesIO

        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = [BytesIO(data)]
            mock_replicate.Client.return_value = mock_client
            full_profile = {"endpoint": "test/model", "media_type": "image"}
            full_profile.update(profile or {})
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(full_profile, tmp_path)
                return engine.run([InputFile(path=Path("b.md"), prompt="test")])

    def test_jpeg_bytes_saved_with_jpg_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\xff\xd8\xff" + b"jpegdata")
        assert results[0].status == "ok"
        assert results[0].path.suffix == ".jpg"
        assert results[0].path.read_bytes() == b"\xff\xd8\xff" + b"jpegdata"

    def test_png_bytes_saved_with_png_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\x89PNG\r\n\x1a\n" + b"pngdata")
        assert results[0].path.suffix == ".png"

    def test_gif_bytes_saved_with_gif_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"GIF89a" + b"gifdata")
        assert results[0].path.suffix == ".gif"

    def test_webp_bytes_saved_with_webp_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"RIFF\x00\x00\x00\x00WEBP" + b"data")
        assert results[0].path.suffix == ".webp"

    def test_mp4_bytes_saved_with_mp4_extension(self, tmp_path):
        results = self._run_stream(tmp_path, b"\x00\x00\x00\x18ftyp" + b"mp4data")
        assert results[0].path.suffix == ".mp4"

    def test_unknown_bytes_use_output_format_param(self, tmp_path):
        results = self._run_stream(
            tmp_path,
            b"\xde\xad\xbe\xef",
            profile={"parameters": {"output_format": "jpg"}},
        )
        assert results[0].path.suffix == ".jpg"

    def test_unknown_bytes_fall_back_to_media_type_default(self, tmp_path):
        results = self._run_stream(tmp_path, b"\xde\xad\xbe\xef")
        assert results[0].path.suffix == ".png"


class TestPromptLogging:
    def test_markdown_filename_is_emitted_to_progress(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.return_value = []
            mock_replicate.Client.return_value = mock_client
            progress_calls = []
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "prompt_prefix": "PREFIX: ",
                        "prompt_suffix": " :SUFFIX",
                    },
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="hello")])

            markdowns = [c.message for c in progress_calls if "Markdown:" in c.message]
            assert markdowns == ["[1/1] 📝 Markdown: b.md"]
            assert not any("Prompt:" in c.message for c in progress_calls)

    def test_empty_prompt_is_not_logged(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_replicate.Client.return_value = mock_client
            progress_calls = []
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="   ")])

            assert not any("Prompt:" in c.message for c in progress_calls)


class TestExpectedPath:
    """Error results must carry the destination the run would have used."""

    def _run_error(self, tmp_path, profile: dict | None = None):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = RuntimeError("API timeout")
            mock_replicate.Client.return_value = mock_client
            full_profile = {"endpoint": "test/model", "media_type": "image"}
            full_profile.update(profile or {})
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(full_profile, tmp_path)
                return engine.run([InputFile(path=Path("b.md"), prompt="test")])

    def test_exception_error_carries_expected_path(self, tmp_path):
        results = self._run_error(tmp_path)
        assert results[0].status == "error"
        assert results[0].expected_path is not None
        assert results[0].expected_path.parent == tmp_path
        assert results[0].expected_path.name.startswith("2")
        assert results[0].expected_path.stem.endswith("-b-0")

    def test_expected_path_uses_output_format_extension(self, tmp_path):
        results = self._run_error(tmp_path, profile={"parameters": {"output_format": "jpg"}})
        assert results[0].expected_path.suffix == ".jpg"

    def test_empty_prompt_error_carries_expected_path(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_replicate.Client.return_value = MagicMock()
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine({"endpoint": "test/model", "media_type": "image"}, tmp_path)
                results = engine.run([InputFile(path=Path("b.md"), prompt="  ")])
        assert results[0].status == "error"
        assert results[0].expected_path is not None
        assert results[0].expected_path.stem.endswith("-b-0")

    def test_error_event_carries_api_payload(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = RuntimeError("API timeout")
            mock_replicate.Client.return_value = mock_client
            progress_calls = []
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "video"},
                    tmp_path,
                    on_progress=progress_calls.append,
                )
                engine.run([InputFile(path=Path("b.md"), prompt="test")])

        errors = [c for c in progress_calls if c.level == "error"]
        assert errors
        assert errors[0].api_payload is not None
        assert errors[0].api_payload["prompt"] == "test"


class TestOutputNaming:
    """An embedded timestamp in the stem is replaced, never double-dated.

    A bullet reused across runs (its filename already carries
    ``YYMMDD_HHMMSS``) keeps one date in the output name; stems without one
    keep the historical ``{ts}-{stem}`` prepend.
    """

    STEM = "0099_261014_125811-81_rw-80-1-0"
    TS = "261015_150435"
    NAMED = "0099_261015_150435-81_rw-80-1-0"

    def _run_pinned(self, tmp_path, source_name: str, output=None, error: str | None = None):
        """Run one bullet with ``datetime.now()`` pinned to TS."""
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            if error:
                mock_client.run.side_effect = RuntimeError(error)
            else:
                mock_client.run.return_value = output
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("engine_replicate.engine.datetime") as mock_dt:
                    mock_dt.now.return_value.strftime.return_value = self.TS
                    engine = Engine(
                        {"endpoint": "test/model", "media_type": "image"},
                        tmp_path,
                    )
                    return engine.run([InputFile(path=Path(source_name), prompt="test")])

    def test_helper_replaces_first_embedded_timestamp(self):
        from engine_replicate.engine import _output_stem

        assert _output_stem(self.STEM, self.TS) == self.NAMED

    def test_helper_replaces_only_the_first_of_two_timestamps(self):
        from engine_replicate.engine import _output_stem

        assert (
            _output_stem("a_261014_125811_b_260101_090000", self.TS)
            == "a_261015_150435_b_260101_090000"
        )

    def test_helper_prepends_without_embedded_timestamp(self):
        from engine_replicate.engine import _output_stem

        assert _output_stem("b", self.TS) == f"{self.TS}-b"

    def test_saved_path_replaces_embedded_timestamp(self, tmp_path):
        with patch("urllib.request.urlretrieve"):
            results = self._run_pinned(
                tmp_path, f"{self.STEM}.md", ["https://example.com/img.png"]
            )
        assert results[0].status == "ok"
        assert results[0].path.name == f"{self.NAMED}-0.png"

    def test_saved_path_prepends_without_embedded_timestamp(self, tmp_path):
        with patch("urllib.request.urlretrieve"):
            results = self._run_pinned(tmp_path, "b.md", ["https://example.com/img.png"])
        assert results[0].status == "ok"
        assert results[0].path.name == f"{self.TS}-b-0.png"

    def test_expected_path_replaces_embedded_timestamp(self, tmp_path):
        results = self._run_pinned(tmp_path, f"{self.STEM}.md", error="API timeout")
        assert results[0].status == "error"
        assert results[0].expected_path.name == f"{self.NAMED}-0.png"

    def test_multi_output_suffix_kept_with_embedded_timestamp(self, tmp_path):
        with patch("urllib.request.urlretrieve"):
            results = self._run_pinned(
                tmp_path,
                f"{self.STEM}.md",
                ["https://example.com/a.png", "https://example.com/b.png"],
            )
        assert [r.path.name for r in results] == [
            f"{self.NAMED}-0_0.png",
            f"{self.NAMED}-0_1.png",
        ]


class TestImports:
    def test_init_exports_all_names(self):
        from engine_replicate import (
            Engine,
            EngineError,
            InputFile,
            OutputFile,
            ProgressEvent,
        )

        assert Engine is not None
        assert InputFile is not None
        assert OutputFile is not None
        assert ProgressEvent is not None
        assert EngineError is not None


class TestBuildReplicateInput:
    """Slot routing + per-Markdown-file duration override for video payloads."""

    def _build(self, profile, item, params=None, schema=None):
        engine = Engine(profile, "/tmp/out")
        if schema is not None:
            engine._input_props_cache = {
                key: engine._resolve_schema_refs(value, schema)
                for key, value in schema.items()
            }
        return engine._build_replicate_input(params or {}, item.prompt, item, "video")

    SEEDANCE_SCHEMA = {
        "image": {"type": "string"},
        "reference_images": {"type": "array"},
        "reference_videos": {"type": "array"},
        "reference_audios": {"type": "array"},
    }

    def test_seedance_shaped_payload_dict(self):
        profile = {
            "endpoint": "bytedance/seedance-1-pro",
            "image_url_param": "image",
            "duration_param_name": "duration",
        }
        item = InputFile(
            path=Path("b.md"),
            prompt="a cinematic pan",
            reference_urls=["https://ref.com/start.jpg"],
            references={"reference_images": ["https://ref.com/img.jpg"]},
            metadata={"duration": "auto"},
        )
        payload = self._build(profile, item, schema=self.SEEDANCE_SCHEMA)
        assert payload == {
            "prompt": "a cinematic pan",
            "image": "https://ref.com/start.jpg",
            "reference_images": ["https://ref.com/img.jpg"],
            "duration": "auto",
        }

    def test_primary_key_prefers_image_url_param(self):
        profile = {
            "image_url_param": "start_image",
            "reference_param": "images",
        }
        item = InputFile(path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"])
        payload = self._build(profile, item)
        assert payload["start_image"] == "https://x.com/a.jpg"
        assert "images" not in payload

    def test_primary_key_falls_back_to_reference_param(self):
        profile = {"reference_param": "images"}
        item = InputFile(path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"])
        payload = self._build(profile, item)
        assert payload["images"] == "https://x.com/a.jpg"

    def test_primary_key_falls_back_to_image_input(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"])
        payload = self._build(profile, item)
        assert payload["image_input"] == "https://x.com/a.jpg"

    def test_named_slots_route_to_own_keys(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            references={
                "reference_images": ["https://x.com/i.jpg"],
                "reference_videos": ["https://x.com/v.mp4"],
            },
        )
        payload = self._build(profile, item)
        assert payload["reference_images"] == "https://x.com/i.jpg"
        assert payload["reference_videos"] == "https://x.com/v.mp4"

    def test_schema_string_param_gets_single_url(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p", reference_urls=["https://x.com/a.jpg"])
        payload = self._build(profile, item, schema={"image_input": {"type": "string"}})
        assert payload["image_input"] == "https://x.com/a.jpg"

    def test_schema_array_param_keeps_list_with_one_url(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            references={"reference_images": ["https://x.com/i.jpg"]},
        )
        payload = self._build(profile, item, schema={"reference_images": {"type": "array"}})
        assert payload["reference_images"] == ["https://x.com/i.jpg"]

    def test_multiple_urls_fallback_keeps_list(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            reference_urls=["https://x.com/a.jpg", "https://x.com/b.jpg"],
        )
        payload = self._build(profile, item)
        assert payload["image_input"] == ["https://x.com/a.jpg", "https://x.com/b.jpg"]

    def test_multiple_urls_on_string_param_raises_loudly(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(
            path=Path("b.md"),
            prompt="p",
            reference_urls=["https://x.com/a.jpg", "https://x.com/b.jpg"],
        )
        with pytest.raises(EngineError, match="single media URL"):
            self._build(profile, item, schema={"image_input": {"type": "string"}})

    def test_empty_named_list_omitted_by_default(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p", references={"reference_images": []})
        payload = self._build(profile, item)
        assert "reference_images" not in payload

    def test_empty_named_list_emitted_when_required(self):
        profile = {"endpoint": "test/model", "required_slots": ["reference_images"]}
        item = InputFile(path=Path("b.md"), prompt="p", references={"reference_images": []})
        payload = self._build(profile, item)
        assert payload["reference_images"] == []

    def test_duration_override_verbatim_int(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={"duration": 7})
        payload = self._build(profile, item)
        assert payload["duration"] == 7

    def test_duration_token_stays_string(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={"duration": "auto"})
        payload = self._build(profile, item)
        assert payload["duration"] == "auto"

    def test_no_metadata_duration_leaves_params_untouched(self):
        profile = {"endpoint": "test/model", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={})
        payload = self._build(profile, item, params={"aspect_ratio": "16:9"})
        assert "duration" not in payload
        assert payload["aspect_ratio"] == "16:9"

    PVIDEO_SCHEMA = {
        "aspect_ratio": {"type": "string"},
        "duration": {"type": "integer"},
        "resolution": {"type": "string"},
        "fps": {"type": "integer"},
        "draft": {"type": "boolean"},
    }

    def test_numeric_string_params_coerced_against_schema(self):
        profile = {"endpoint": "prunaai/p-video"}
        item = InputFile(path=Path("b.md"), prompt="p")
        params = {
            "aspect_ratio": "16:9",
            "duration": "5",
            "resolution": "720p",
            "fps": "24",
            "draft": False,
            "prompt_upsampling": True,
        }
        payload = self._build(profile, item, params=params, schema=self.PVIDEO_SCHEMA)
        assert payload["duration"] == 5
        assert payload["fps"] == 24
        assert payload["aspect_ratio"] == "16:9"
        assert payload["resolution"] == "720p"
        assert payload["draft"] is False
        assert payload["prompt_upsampling"] is True

    def test_quoted_boolean_param_coerced_against_schema(self):
        profile = {"endpoint": "prunaai/p-video"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(
            profile, item, params={"draft": "false"}, schema=self.PVIDEO_SCHEMA
        )
        assert payload["draft"] is False

    def test_non_numeric_string_on_integer_param_passes_verbatim(self):
        profile = {"endpoint": "prunaai/p-video"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(
            profile, item, params={"duration": "auto"}, schema=self.PVIDEO_SCHEMA
        )
        assert payload["duration"] == "auto"

    def test_duration_override_coerced_against_schema(self):
        profile = {"endpoint": "prunaai/p-video", "duration_param_name": "duration"}
        item = InputFile(path=Path("b.md"), prompt="p", metadata={"duration": "10"})
        payload = self._build(profile, item, schema=self.PVIDEO_SCHEMA)
        assert payload["duration"] == 10

    def test_missing_schema_passes_params_verbatim(self):
        profile = {"endpoint": "test/model"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(profile, item, params={"duration": "5"}, schema={})
        assert payload["duration"] == "5"

    FPS_REF_SCHEMA = {
        "components": {"schemas": {"fps": {"type": "integer", "enum": [24, 48]}}},
        "duration": {"type": "integer"},
        "fps": {"allOf": [{"$ref": "#/components/schemas/fps"}], "default": 24},
    }

    def test_allof_ref_param_coerced(self):
        profile = {"endpoint": "prunaai/p-video"}
        item = InputFile(path=Path("b.md"), prompt="p")
        payload = self._build(
            profile, item, params={"fps": "24", "duration": "5"}, schema=self.FPS_REF_SCHEMA
        )
        assert payload["fps"] == 24
        assert payload["duration"] == 5

    def test_resolve_schema_refs_merges_ref_and_own_keys(self):
        engine = Engine({"endpoint": "test/model"}, "/tmp/out")
        resolved = engine._resolve_schema_refs(
            {"allOf": [{"$ref": "#/components/schemas/fps"}], "default": 24},
            self.FPS_REF_SCHEMA,
        )
        assert resolved == {"type": "integer", "enum": [24, 48], "default": 24}


class TestListStandbyProfiles:
    def test_vid_shelf_returns_only_video_yamls(self):
        from engine_replicate import list_standby_profiles

        vids = list_standby_profiles("VID")
        assert vids
        assert all("VID" in p.parts for p in vids)
        names = {p.name for p in vids}
        assert "p-video_16x9_720p_5s_24fps.yaml" in names

    def test_img_shelf_returns_only_image_yamls(self):
        from engine_replicate import list_standby_profiles

        imgs = list_standby_profiles("IMG")
        assert imgs
        assert all("IMG" in p.parts for p in imgs)

    def test_no_filter_returns_every_shelf(self):
        from engine_replicate import list_standby_profiles

        all_profiles = list_standby_profiles()
        vids = list_standby_profiles("VID")
        imgs = list_standby_profiles("IMG")
        assert len(all_profiles) == len(vids) + len(imgs)

    def test_unknown_media_type_returns_empty(self):
        from engine_replicate import list_standby_profiles

        assert list_standby_profiles("TXT") == []


class _FakeError(Exception):
    def __init__(self, status=None, detail=""):
        self.status = status
        self.detail = detail
        super().__init__(detail or str(status))


class TestErrorClassification:
    def _engine(self):
        return Engine({"endpoint": "test/model"}, "/tmp/out")

    def test_e003_high_demand_is_retryable(self):
        err = _FakeError(
            detail=(
                "Prediction failed: Async prediction failed: ModelRateLimitError: "
                "Service is currently unavailable due to high demand. Please try "
                "again later. (E003)"
            )
        )
        eng = self._engine()
        assert eng._is_rate_limited(err)
        assert Engine._classify_error(err) == "retryable"
        assert eng._is_high_demand(err)

    def test_http_429_is_retryable(self):
        eng = self._engine()
        assert eng._is_rate_limited(_FakeError(status=429, detail="rate limited"))

    def test_ratelimit_class_string_is_retryable(self):
        eng = self._engine()
        assert eng._is_rate_limited(_FakeError(detail="ModelRateLimitError: boom"))

    def test_insufficient_payment_is_fatal(self):
        eng = self._engine()
        assert eng._is_fatal(_FakeError(detail="Insufficient balance to run model"))

    def test_http_402_is_fatal(self):
        eng = self._engine()
        assert eng._is_fatal(_FakeError(status=402, detail="Payment required"))

    def test_http_401_is_fatal(self):
        eng = self._engine()
        assert eng._is_fatal(_FakeError(status=401, detail="Unauthorized"))

    def test_unknown_5xx_is_neither_retryable_nor_fatal(self):
        eng = self._engine()
        err = _FakeError(status=500, detail="internal error")
        assert not eng._is_rate_limited(err)
        assert not eng._is_fatal(err)


class TestRetryClassificationMatchesE003:
    def test_real_e003_line_is_retried(self, tmp_path):
        """Regression: the exact E003 line from the 2026-09-09 run churn."""
        line = (
            "Prediction failed: Async prediction failed: ModelRateLimitError: "
            "Service is currently unavailable due to high demand. Please try "
            "again later. (E003) (1cah9wlWR9)"
        )
        approver = Engine({"endpoint": "test/model"}, "/tmp/out")
        assert approver._is_rate_limited(_FakeError(detail=line))

        # End-to-end: client.run raises the E003 once, then succeeds.
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = [
                _FakeError(detail=line),
                ["https://example.com/out.png"],
            ]
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("urllib.request.urlretrieve"):
                    engine = Engine(
                        {
                            "endpoint": "test/model",
                            "media_type": "image",
                            "retry_on_429": 2,
                            "retry_base_delay": 0.0,
                        },
                        tmp_path,
                    )
                    results = engine.run(
                        [InputFile(path=Path("b.md"), prompt="hello")]
                    )
                    assert results[0].status == "ok"
                    assert mock_client.run.call_count == 2


class TestCircutBreakerAndFatalAbort:
    def test_circuit_breaker_aborts_after_n_consecutive(self, tmp_path):
        """Sustained E003 failures must stop the batch, not churn all items."""
        line = "AvailabilityError: high demand (E003)"
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = _FakeError(detail=line)
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "retry_on_429": 0,
                        "circuit_breaker_max": 2,
                    },
                    tmp_path,
                )
                inputs = [
                    InputFile(path=Path(f"{i}.md"), prompt=f"p{i}") for i in range(6)
                ]
                results = engine.run(inputs)
                # 2 errors recorded (circuit_max), then batch aborted.
                assert len(results) == 2
                assert all(r.status == "error" for r in results)
                # Did not attempt the remaining 4 items.
                assert mock_client.run.call_count == 2

    def test_fatal_error_aborts_batch_immediately(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = _FakeError(
                status=402, detail="Payment required"
            )
            mock_replicate.Client.return_value = mock_client
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image"},
                    tmp_path,
                )
                inputs = [
                    InputFile(path=Path(f"{i}.md"), prompt=f"p{i}") for i in range(5)
                ]
                results = engine.run(inputs)
                assert len(results) == 1
                assert results[0].status == "error"
                assert mock_client.run.call_count == 1


class TestErrorLog:
    """Every per-item failure writes a verbose `<base>-<idx>_ERROR.log`."""

    def _run(
        self,
        tmp_path,
        *,
        exc: Exception | None = None,
        output="https://example.com/out.png",
        prompt: str = "test",
        profile: dict | None = None,
        item: InputFile | None = None,
    ):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            if exc is not None:
                mock_client.run.side_effect = exc
            else:
                mock_client.run.return_value = output
            mock_replicate.Client.return_value = mock_client
            full_profile = {"endpoint": "test/model", "media_type": "image"}
            full_profile.update(profile or {})
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(full_profile, tmp_path)
                return engine.run([item or InputFile(path=Path("b.md"), prompt=prompt)])

    def test_provider_exception_writes_error_log(self, tmp_path):
        results = self._run(tmp_path, exc=RuntimeError("API timeout"))
        result = results[0]
        assert result.status == "error"

        log_path = Path(result.metadata["error_log"])
        assert log_path.name == f"{result.expected_path.stem}_ERROR.log"
        assert log_path.parent == result.expected_path.parent

        content = log_path.read_text(encoding="utf-8")
        assert content.startswith(f"{log_path.name} — generation failure")
        assert "item: 1 of 1" in content
        assert f"source: {Path('b.md').resolve()}" in content
        assert f"expected: {result.expected_path.resolve()}" in content
        assert "platform: replicate" in content
        assert "endpoint: test/model" in content
        assert "error stage: provider_call" in content
        assert "error: RuntimeError: API timeout" in content
        assert "Traceback (most recent call last):" in content
        assert '"prompt": "test"' in content
        assert "api payload:" in content

    def test_no_output_writes_error_log(self, tmp_path):
        results = self._run(tmp_path, output=None)
        result = results[0]
        assert result.status == "error"

        content = Path(result.metadata["error_log"]).read_text(encoding="utf-8")
        assert "error stage: no_output" in content
        assert "error: No output returned from replicate" in content
        assert "traceback: n/a" in content
        assert '"prompt": "test"' in content

    def test_empty_prompt_writes_error_log(self, tmp_path):
        results = self._run(tmp_path, prompt="   ")
        result = results[0]
        assert result.status == "error"

        content = Path(result.metadata["error_log"]).read_text(encoding="utf-8")
        assert "error stage: empty_prompt" in content
        assert "error: Empty prompt after applying prefix/suffix" in content
        assert "traceback: n/a" in content
        assert "api payload:\nnull" in content

    def test_save_download_failure_writes_error_log(self, tmp_path):
        with patch("urllib.request.urlretrieve", side_effect=OSError("disk full")):
            results = self._run(tmp_path)
        result = results[0]
        assert result.status == "error"

        content = Path(result.metadata["error_log"]).read_text(encoding="utf-8")
        assert "error stage: save" in content
        assert "error: OSError: disk full" in content
        assert "Traceback (most recent call last):" in content

    def test_error_log_created_in_missing_relative_dir(self, tmp_path):
        item = InputFile(
            path=Path("b.md"),
            prompt="test",
            metadata={"relative_dir": "scene1/nested"},
        )
        results = self._run(tmp_path, exc=RuntimeError("boom"), item=item)
        log_path = Path(results[0].metadata["error_log"])
        assert log_path.parent == tmp_path / "scene1" / "nested"
        assert log_path.exists()

    def test_error_log_masks_key_like_values(self, tmp_path):
        results = self._run(
            tmp_path,
            exc=RuntimeError("boom r8_offendingtoken99"),
            profile={"api_key": "r8_secretvalue123"},
        )
        content = Path(results[0].metadata["error_log"]).read_text(encoding="utf-8")
        assert "r8_secretvalue123" not in content
        assert "r8_offendingtoken99" not in content
        assert "***MASKED***" in content

    def test_success_writes_no_error_log(self, tmp_path):
        with patch("urllib.request.urlretrieve"):
            results = self._run(tmp_path)
        assert results[0].status == "ok"
        assert results[0].metadata.get("error_log") is None
        assert list(tmp_path.glob("*_ERROR.log")) == []
