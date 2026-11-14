"""A 5xx never loses or duplicates a prediction; a long prompt is refused before any call."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from engine_replicate import Engine, InputFile
from engine_replicate import retry_policy as rp


class Err(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"status {status}")


def pred(status="succeeded", output=None):
    return SimpleNamespace(
        id="p1", status=status, error=None, output=output,
        urls={"get": "http://r/v1/predictions/p1", "web": "https://replicate.com/p/p1"},
    )  # fmt: skip


class Client:
    def __init__(self, create, gets):
        self.poll_interval = 0
        self.creates = 0
        self.gets = 0
        self._create, self._gets = list(create), list(gets)
        self.models = SimpleNamespace(predictions=SimpleNamespace(create=self.create))
        self.predictions = SimpleNamespace(get=self.get)

    def create(self, **kw):
        assert "wait" not in kw, "create must return at once so the id is kept"
        self.creates += 1
        r = self._create.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, _id):
        self.gets += 1
        r = self._gets.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def engine(tmp_path, **profile):
    p = {"endpoint": "minimax/h3", "media_type": "video", "retry_base_delay": 0,
         "retry_on_429": 2, **profile}  # fmt: skip
    return Engine(p, tmp_path, api_key="r8_x")


@pytest.fixture(autouse=True)
def no_sleep():
    with patch.object(rp.time, "sleep"):
        yield


def test_poll_5xx_repolls_same_prediction(tmp_path):
    c = Client([pred("starting")], [Err(502), Err(504), pred(output="u")])
    made = {}
    assert engine(tmp_path)._request_with_retry(c, "minimax/h3", {}, "[1/1]", made) == "u"
    assert c.creates == 1 and c.gets == 3 and made["id"] == "p1"


def test_create_5xx_is_retried(tmp_path):
    c = Client([Err(502), Err(504), pred(output="u")], [])
    made = {}
    assert engine(tmp_path)._request_with_retry(c, "minimax/h3", {}, "[1/1]", made) == "u"
    assert c.creates == 3


def test_poll_gives_up_after_bounded_tries_without_recreating(tmp_path):
    c = Client([pred("starting")], [Err(502)] * 3)
    made = {}
    with pytest.raises(Err):
        engine(tmp_path)._request_with_retry(c, "minimax/h3", {}, "[1/1]", made)
    assert c.creates == 1 and c.gets == 3 and made["id"] == "p1"


def test_lost_prediction_is_logged_and_listed(tmp_path):
    c = Client([pred("starting")], [Err(502)] * 3)
    said = []
    eng = Engine(
        {"endpoint": "minimax/h3", "media_type": "video", "retry_base_delay": 0, "retry_on_429": 2},
        tmp_path, api_key="r8_x", on_progress=lambda e: said.append(e.message),
    )  # fmt: skip
    item = InputFile(path=tmp_path / "a.md", prompt="hello")
    with patch("replicate.Client", return_value=c):
        res = eng.run([item])
    assert res[0].status == "error"
    log = open(res[0].metadata["error_log"]).read()
    assert "prediction id: p1" in log and "/v1/predictions/p1" in log
    assert "replicate.com/p/p1" in log
    assert "may still finish at Replicate" in said[-1] and "p1" in said[-1]


def test_long_prompt_refused_before_any_call(tmp_path):
    c = Client([], [])
    item = InputFile(path=tmp_path / "a.md", prompt="x" * 7001)
    with patch("replicate.Client", return_value=c):
        res = engine(tmp_path).run([item])
    assert c.creates == 0
    assert "7000" in res[0].error_msg and "7001" in res[0].error_msg


def test_limit_declared_for_all_h3_endpoints(tmp_path):
    for ep in ("minimax/h3", "minimax/h3-highspeed", "minimax/h3-lite"):
        with pytest.raises(ValueError, match="7000"):
            engine(tmp_path, endpoint=ep)._check_prompt_length("x" * 7001)
        engine(tmp_path, endpoint=ep)._check_prompt_length("x" * 7000)


def test_numeric_text_does_not_override_a_non_5xx_status():
    class E(Exception):
        status = 422

    exc = E("width must be <= 512")
    assert not rp.RetryPolicy._is_server_error(exc)
    assert rp.RetryPolicy._classify_error(exc) == "other"
