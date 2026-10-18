import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_replicate import Engine, EngineError, InputFile  # noqa: E402
from test_engine import _FakeError  # noqa: E402
class TestConcurrencyKey:
    """Regression gate: `concurrency` defaults to 1, absent = 1, never sent."""

    @staticmethod
    def _frozen_datetime():
        from datetime import datetime as _dt

        class _Frozen(_dt):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 18, 12, 0, 0)

        return _Frozen

    @classmethod
    def _run_batch(cls, profile, out_dir):
        mock_replicate = MagicMock()
        mock_client = MagicMock()
        mock_client.run.side_effect = [
            ["https://a.com/ok-1.png"],
            RuntimeError("fail"),
            ["https://a.com/ok-3.png"],
        ]
        mock_replicate.Client.return_value = mock_client
        progress = []
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                with patch("urllib.request.urlretrieve"):
                    with patch(
                        "engine_replicate.engine.datetime",
                        cls._frozen_datetime(),
                    ):
                        engine = Engine(profile, out_dir, on_progress=progress.append)
                        results = engine.run(
                            [
                                InputFile(path=Path(f"b{i}.md"), prompt=f"p{i}")
                                for i in range(3)
                            ]
                        )
        calls = [c.kwargs["input"] for c in mock_client.run.call_args_list]
        return results, progress, calls

    def test_absent_equals_one_batch_identical(self, tmp_path):
        base = {
            "endpoint": "test/model",
            "media_type": "image",
            "parameters": {"width": 512},
        }
        r0, p0, c0 = self._run_batch(base, tmp_path / "absent")
        r1, p1, c1 = self._run_batch(
            {**base, "concurrency": 1}, tmp_path / "one"
        )
        assert len(r0) == len(r1) == 3
        assert [r.status for r in r0] == [r.status for r in r1]
        assert [r.error_msg for r in r0] == [r.error_msg for r in r1]
        assert [r.source_path for r in r0] == [r.source_path for r in r1]
        names0 = [(r.path or r.expected_path).name for r in r0]
        names1 = [(r.path or r.expected_path).name for r in r1]
        assert names0 == names1
        assert [(e.message, e.level, e.current, e.total) for e in p0] == [
            (e.message, e.level, e.current, e.total) for e in p1
        ]
        assert c0 == c1
        statuses = [r.status for r in r0]
        assert statuses.count("ok") == 2 and statuses.count("error") == 1

    def test_concurrency_never_sent_to_provider(self, tmp_path):
        profile = {
            "endpoint": "test/model",
            "media_type": "image",
            "concurrency": 1,
            "parameters": {"width": 512},
        }
        _, _, calls = self._run_batch(profile, tmp_path)
        assert calls
        assert all("concurrency" not in payload for payload in calls)

    def test_fatal_error_identity_absent_vs_one(self, tmp_path):
        profiles = [
            {"endpoint": "test/model", "media_type": "image"},
            {
                "endpoint": "test/model",
                "media_type": "image",
                "concurrency": 1,
            },
        ]
        outcomes = []
        for i, profile in enumerate(profiles):
            mock_replicate = MagicMock()
            mock_client = MagicMock()
            mock_client.run.side_effect = _FakeError(
                status=402, detail="Payment required"
            )
            mock_replicate.Client.return_value = mock_client
            progress = []
            with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
                with patch.dict("sys.modules", {"replicate": mock_replicate}):
                    engine = Engine(profile, tmp_path / f"fatal{i}", on_progress=progress.append)
                    results = engine.run(
                        [InputFile(path=Path(f"{n}.md"), prompt="p") for n in range(3)]
                    )
            outcomes.append(
                (
                    [r.status for r in results],
                    [r.error_msg for r in results],
                    [(e.message, e.level) for e in progress],
                    mock_client.run.call_count,
                )
            )
        assert outcomes[0] == outcomes[1]
        assert outcomes[0][3] == 1

    @pytest.mark.parametrize("bad", [0, -1, "two", True, 1.5])
    def test_invalid_concurrency_raises_loudly(self, tmp_path, bad):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                engine = Engine(
                    {"endpoint": "test/model", "media_type": "image", "concurrency": bad},
                    tmp_path,
                )
                with pytest.raises(EngineError, match="concurrency"):
                    engine.run([])

    def test_valid_values_parse_and_default(self, tmp_path):
        with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
            mock_replicate = MagicMock()
            with patch.dict("sys.modules", {"replicate": mock_replicate}):
                absent = Engine(
                    {"endpoint": "test/model", "media_type": "image"}, tmp_path
                )
                explicit = Engine(
                    {
                        "endpoint": "test/model",
                        "media_type": "image",
                        "concurrency": 2,
                    },
                    tmp_path,
                )
                assert absent._concurrency == 1
                assert explicit._concurrency == 2

