"""Request, retry and error policy — split out of the gold Engine.

Mixin methods on Engine; names and behavior are unchanged
(`_request_with_retry` keeps its exponential backoff with jitter, the
stretched high-demand backoff, and Retry-After honoring; the classifier
is untouched).
"""

from typing import Any  # noqa: TC003 - supported Python versions

import os
import random
import re
import time
from email.utils import parsedate_to_datetime


def _retry_after_seconds(exc):
    """Seconds named by an exception's Retry-After header, or None.

    Guards every malformed shape: missing headers, None, a non-mapping
    (a string, a list), or an unparseable value. Numeric strings and the
    standard HTTP-date form are both honored; backoff always sleeps at
    LEAST the requested value — the max semantics stay.
    """
    headers = getattr(exc, "headers", None)
    if not headers or not hasattr(headers, "get"):
        return None
    value = None
    try:
        for key in headers:
            if str(key).lower() == "retry-after":
                value = headers.get(key)
                break
    except Exception:
        return None
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone

        when = parsedate_to_datetime(text)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class RetryPolicy:
    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        return os.environ.get(self.API_KEY_ENV_VAR, "")

    def _with_retry(self, call, prefix: str):
        max_retries = int(self._profile.get("retry_on_429", 4))
        base_delay = float(self._profile.get("retry_base_delay", 2.0))
        for attempt in range(max_retries + 1):
            try:
                return call()
            except Exception as exc:
                rate = self._classify_error(exc)
                if rate != "retryable" or attempt >= max_retries:
                    raise
                long_backoff = self._is_high_demand(exc)
                sleep = base_delay * (8 if long_backoff else 2) ** attempt + random.uniform(
                    0, 1
                )
                requested = _retry_after_seconds(exc)
                if requested is not None:
                    sleep = max(sleep, requested)
                if long_backoff:
                    label = "high-demand outage"
                elif self._is_timeout(exc):
                    label = "request timeout"
                elif self._is_server_error(exc):
                    label = "server error"
                else:
                    label = "429 rate limit"
                self._emit(
                    f"{prefix} ⏳ {label}, retry {attempt + 1}/{max_retries} in {sleep:.1f}s"
                )
                time.sleep(sleep)

    def _request_with_retry(
        self, client, endpoint: str, replicate_input: dict, prefix: str, made: dict
    ) -> Any:
        """Create the prediction, then poll its id until it ends.

        `made` is filled with the prediction's id/get/web the moment it exists, so
        the caller can name it in an error log. Only the create call is ever
        repeated as a create: a failure while polling re-polls the same id, never
        makes a second (billed) prediction.
        """
        if ":" in endpoint:
            version = endpoint.split(":", 1)[1]
            create = lambda: client.predictions.create(  # noqa: E731
                version=version, input=replicate_input
            )
        else:
            owner, name = endpoint.split("/", 1)
            create = lambda: client.models.predictions.create(  # noqa: E731
                model=(owner, name), input=replicate_input
            )
        prediction = self._with_retry(create, prefix)
        urls = getattr(prediction, "urls", None) or {}
        made.update(id=prediction.id, get=urls.get("get", ""), web=urls.get("web", ""))
        self._emit(f"{prefix} 🆔 Prediction {prediction.id}")
        interval = getattr(client, "poll_interval", 0.5)
        interval = float(interval) if isinstance(interval, (int, float)) else 0.5
        while prediction.status not in ("succeeded", "failed", "canceled"):
            time.sleep(interval)
            prediction = self._with_retry(
                lambda: client.predictions.get(made["id"]), prefix
            )
        if prediction.status != "succeeded":
            raise RuntimeError(f"Prediction {prediction.status}: {prediction.error}")
        return prediction.output

    @classmethod
    def _classify_error(cls, exc: Exception) -> str:
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
        if status == 429 or cls._is_server_error(exc):
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
    def _is_server_error(exc: Exception) -> bool:
        """Any 5xx, by status or by text (502, 504, "bad gateway", ...)."""
        status = getattr(exc, "status", None)
        if isinstance(status, int):
            return 500 <= status <= 599
        text = str(exc).lower()
        return bool(re.search(r"\b5\d\d\b", text)) or any(
            t in text for t in ("bad gateway", "gateway timeout", "internal server error")
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
