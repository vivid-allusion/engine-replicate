from types import SimpleNamespace
from unittest.mock import MagicMock


class FakeClient:
    """Stands in for replicate.Client: `run` is the scripted create-and-finish step."""

    def __init__(self):
        self.run = MagicMock()
        self.poll_interval = 0
        create = lambda model, input, **_: SimpleNamespace(  # noqa: E731
            id="pred1",
            status="succeeded",
            error=None,
            output=self.run("/".join(model), input=input),
            urls={"get": "http://x/v1/predictions/pred1", "web": "https://replicate.com/p/pred1"},
        )
        self.models = SimpleNamespace(predictions=SimpleNamespace(create=create))
