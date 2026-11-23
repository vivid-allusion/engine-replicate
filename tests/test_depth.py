import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_replicate import Engine, InputFile  # noqa: E402

DEPTH_DIR = Path(__file__).resolve().parent.parent / "engine_replicate/endpoints/DEPTH-Models"
PIC = "https://example.com/street.png"


def test_seven_depth_tomls():
    eps = {}
    for f in DEPTH_DIR.glob("*.toml"):
        d = tomllib.loads(f.read_text())
        assert d["category"] == "DEPTH" and d["platform"] == "replicate"
        assert ":" in d["endpoint"]
        eps[d["endpoint"].split(":")[0]] = d
    assert sorted(eps) == sorted([
        "chenxwh/depth-anything-v2", "cjwbw/depth-anything", "cjwbw/midas", "cjwbw/zoedepth",
        "adirik/marigold", "chenxwh/ml-depth-pro", "chenxwh/lotus",
    ])
    assert eps["chenxwh/ml-depth-pro"]["general"]["image_url_param"] == "image_path"
    assert [o["value"] for o in eps["chenxwh/lotus"]["params"]["task"]["options"]] == ["depth"]


def _run(tmp_path, reply, props, concurrency, prompt=""):
    sent = []

    def create(**kw):
        sent.append(kw)
        return SimpleNamespace(id="p", status="succeeded", error=None, output=reply,
                               urls={"get": "http://x/p", "web": "http://x/p"})

    client = MagicMock(poll_interval=0)
    client.predictions.create = create
    client.models.predictions.create = create
    rep = MagicMock()
    rep.Client.return_value = client
    with patch.dict("os.environ", {"REPLICATE_API_TOKEN": "r8_test"}), patch.dict("sys.modules", {"replicate": rep}):
        e = Engine({"endpoint": "o/n:v", "media_type": "image", "image_url_param": "image",
                    "concurrency": concurrency}, tmp_path)
        e._input_props_cache = props
        res = e.run([InputFile(path=Path("a.md"), prompt=prompt, reference_urls=[PIC])])
    return res, sent


def _png(tmp_path, name):
    f = tmp_path / f"src-{name}.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    return f.as_uri(), f.read_bytes()


@pytest.mark.parametrize("concurrency", [1, 2])
def test_picture_without_words_runs_and_sends_no_prompt(tmp_path, concurrency):
    url, data = _png(tmp_path, "a")
    res, sent = _run(tmp_path / "out", url, {"image": {"type": "string"}}, concurrency)
    assert res[0].status == "ok"
    assert sent[0]["input"] == {"image": PIC}
    assert res[0].path.read_bytes() == data


@pytest.mark.parametrize("concurrency", [1, 2])
def test_picture_without_words_still_refused_when_model_takes_prompt(tmp_path, concurrency):
    res, sent = _run(tmp_path / "out", None, {"prompt": {"type": "string"}, "image": {"type": "string"}}, concurrency)
    assert res[0].status == "error" and "Empty prompt" in res[0].error_msg
    assert not sent


@pytest.mark.parametrize("concurrency", [1, 2])
def test_named_fields_reply(tmp_path, concurrency):
    grey, grey_b = _png(tmp_path, "grey")
    color, color_b = _png(tmp_path, "color")
    npz = tmp_path / "d.npz"
    npz.write_bytes(b"PKnpz")
    props = {"image": {"type": "string"}}
    res, _ = _run(tmp_path / "pro", {"npz": npz.as_uri(), "color_map": color}, props, concurrency)
    files = [p for p in (tmp_path / "pro").iterdir() if p.is_file()]
    assert [p.read_bytes() for p in files] == [color_b] and files[0].suffix == ".png"
    res, _ = _run(tmp_path / "lotus", {"generative": color, "discriminative": grey}, props, concurrency)
    got = {p.read_bytes() for p in (tmp_path / "lotus").iterdir() if p.is_file()}
    assert got == {grey_b, color_b}
