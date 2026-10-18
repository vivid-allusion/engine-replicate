"""Request, retry and error policy — split out of the gold Engine.

Mixin methods on Engine; names and behavior are unchanged
(`_request_with_retry` keeps its exponential backoff with jitter, the
stretched high-demand backoff, and Retry-After honoring; the classifier
is untouched).
"""

from typing import Any  # noqa: TC003 - supported Python versions

import os
import random
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
                long_backoff = self._is_high_demand(exc)
                sleep = base_delay * (8 if long_backoff else 2) ** attempt + random.uniform(
                    0, 1
                )
                requested = _retry_after_seconds(exc)
                if requested is not None:
                    sleep = max(sleep, requested)
                label = "high-demand outage" if long_backoff else "429 rate limit"
                self._emit(
                    f"{prefix} ⏳ {label}, retry {attempt + 1}/{max_retries} in {sleep:.1f}s"
                )
                time.sleep(sleep)
        raise last_exc

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
        if status == 429:
            return "retryable"
        if cls._is_high_demand(exc):
            return "retryable"
        if any(
            token in text
            for token in ("ratelimit", "rate limit", "throttl", "too many")
        ):
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

    def _is_rate_limited(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "retryable"

    def _is_fatal(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "fatal"
