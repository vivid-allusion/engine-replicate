"""Stub-provider proofs for the N-in-flight runner (phase_2 R6).

The stub client records overlapping provider executions through an
atomic counter, so the tests can assert both directions of the cap: more
than one job provably in flight when the profile asks for it, and never
more than N at any point.
"""

import sys
import threading
import time
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_engine import _FakeError  # noqa: E402

from engine_replicate import Engine, InputFile  # noqa: E402
from test_concurrency_key import TestConcurrencyKey  # noqa: E402

_HOLD = 0.03


class _Probe:
    def __init__(self, impl):
        self._impl = impl
        self._lock = threading.Lock()
        self._current = 0
        self.max_seen = 0
        self.calls = 0

    def __call__(self, *args, **kwargs):
        with self._lock:
            self._current += 1
            self.calls += 1
            self.max_seen = max(self.max_seen, self._current)
        try:
            return self._impl(*args, **kwargs)
        finally:
            with self._lock:
                self._current -= 1


def _run_engine(profile, out_dir, impl, probe=False, progress_calls=None):
    mock_replicate = MagicMock()
    mock_client = MagicMock()
    call = _Probe(impl) if probe else impl
    mock_client.run.side_effect = call
    mock_replicate.Client.return_value = mock_client
    recorder = progress_calls if progress_calls is not None else []
    with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}):
        with patch.dict("sys.modules", {"replicate": mock_replicate}):
            with patch("urllib.request.urlretrieve"):
                with patch(
                    "engine_replicate.engine.datetime",
                    TestConcurrencyKey._frozen_datetime(),
                ):
                    engine = Engine(profile, out_dir, on_progress=recorder.append)
                    results = engine.run(inputs_stub())
    return results, recorder, call


def inputs_stub(n=3):
    return [InputFile(path=Path(f"b{i}.md"), prompt=f"p{i}") for i in range(n)]


def ok_impl(endpoint, input):
    time.sleep(_HOLD)
    return [f"https://example.com/{input['prompt']}.png"]


class TestParallelRunner:
    def _parallel_profile(self, **extra):
        return {
            "endpoint": "test/model",
            "media_type": "image",
            "concurrency": 3,
            **extra,
        }

    def test_parallel_batch_matches_concurrency_one(self, tmp_path):
        base = {
            "endpoint": "test/model",
            "media_type": "image",
            "parameters": {"width": 512},
        }
        seq_results, seq_progress, _ = _run_engine(
            dict(base), tmp_path / "seq", ok_impl
        )
        par_results, par_progress, _ = _run_engine(
            self._parallel_profile(parameters={"width": 512}),
            tmp_path / "par",
            ok_impl,
        )
        assert [r.status for r in seq_results] == [r.status for r in par_results]
        assert [r.source_path for r in seq_results] == [
            r.source_path for r in par_results
        ]
        assert [r.path.name for r in seq_results] == [r.path.name for r in par_results]
        seq_msgs = [(e.message, e.level, e.current, e.total) for e in seq_progress]
        par_msgs = [(e.message, e.level, e.current, e.total) for e in par_progress]
        assert sorted(seq_msgs) == sorted(par_msgs)
        assert len(par_results) == 3

    def test_more_than_one_job_in_flight(self, tmp_path):
        _, _, probed = _run_engine(
            self._parallel_profile(), tmp_path, ok_impl, probe=True
        )
        assert probed.max_seen >= 2

    def test_never_more_than_n_in_flight(self, tmp_path):
        _, _, probed = _run_engine(
            self._parallel_profile(concurrency=2), tmp_path, ok_impl, probe=True
        )
        assert probed.max_seen == 2
        _, _, probed3 = _run_engine(
            self._parallel_profile(), tmp_path / "n3", ok_impl, probe=True
        )
        assert probed3.max_seen <= 3

    def test_failing_item_leaves_in_flight_siblings_intact(self, tmp_path):
        def impl(endpoint, input):
            time.sleep(_HOLD)
            if input["prompt"] == "p1":
                raise RuntimeError("provider blew up")
            return [f"https://example.com/{input['prompt']}.png"]

        results, progress, _ = _run_engine(
            self._parallel_profile(), tmp_path, impl
        )
        statuses = [r.status for r in results]
        assert statuses == ["ok", "error", "ok"]
        assert "provider blew up" in results[1].error_msg
        assert any("b0.md" in e.message for e in progress)
        assert any("b2.md" in e.message for e in progress)

    def test_rate_limit_backoff_completes_batch(self, tmp_path):
        attempts = {}

        def impl(endpoint, input):
            prompt = input["prompt"]
            attempts[prompt] = attempts.get(prompt, 0) + 1
            if attempts[prompt] == 1:
                raise _FakeError(status=429, detail="Slow down")
            return [f"https://example.com/{prompt}.png"]

        results, progress, _ = _run_engine(
            self._parallel_profile(
                retry_base_delay=0.05, retry_on_429=3
            ),
            tmp_path,
            impl,
        )
        assert [r.status for r in results] == ["ok", "ok", "ok"]
        assert attempts == {"p0": 2, "p1": 2, "p2": 2}
        assert any("429 rate limit, retry" in e.message for e in progress)

    def test_circuit_breaker_ends_batch_cleanly(self, tmp_path):
        def impl(endpoint, input):
            raise _FakeError(status=429, detail="Too many requests")

        results, progress, probed = _run_engine(
            self._parallel_profile(
                retry_base_delay=0.05, retry_on_429=0, circuit_breaker_max=2
            ),
            tmp_path,
            impl,
            probe=True,
        )
        assert probed.calls < len(inputs_stub(3)) + 1
        assert probed.calls >= 2
        assert any("Circuit breaker tripped" in e.message for e in progress)
        assert results
        assert all(r.status == "error" for r in results)
        assert any("b0.md" in e.message for e in progress)

    def test_progress_lines_name_file_and_item(self, tmp_path):
        progress = []
        _run_engine(
            self._parallel_profile(), tmp_path, ok_impl, progress_calls=progress
        )
        assert any("📝 Markdown: b1.md" in e.message for e in progress)
        assert any(e.message.startswith("[2/3]") for e in progress)
