"""Paid-API and subscription transports for the editorial ladder, and the
spend ledger that caps them.

Standard library only, like the rest of the bot. Nothing here knows about news
items or quality gates: the caller builds the prompt and judges the reply, so
every tier is held to the same checks.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

VIETNAM_TZ = dt.timezone(dt.timedelta(hours=7), "Asia/Saigon")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
EDITORIAL_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["title", "summary"],
    "additionalProperties": False,
}
# These mean the key will not work until a person acts, so retrying only
# burns time; the API is switched off for API_OFF_SECONDS instead.
FATAL_ERROR_CODES = {"invalid_api_key", "insufficient_quota", "account_deactivated",
                     "billing_hard_limit_reached", "billing_not_active"}
API_OFF_SECONDS = 30 * 60
KEEP_DAYS = 14
KEEP_HOURS = 48
# Charged when a model has no price in config: the dearest current rate, so an
# unpriced model can never slip past the daily cap as free.
FALLBACK_PRICE = {"input": 10.0, "output": 50.0}


def vietnam_day(now: float) -> str:
    return dt.datetime.fromtimestamp(now, VIETNAM_TZ).date().isoformat()


def vietnam_hour(now: float) -> str:
    return dt.datetime.fromtimestamp(now, VIETNAM_TZ).strftime("%Y-%m-%dT%H")


class ApiError(Exception):
    """A failed Responses API call. usage is set when tokens were billed anyway."""

    def __init__(self, kind: str, fatal: bool = False, retryable: bool = False,
                 retry_after: float = 0.0, usage: dict | None = None):
        super().__init__(kind)
        self.kind = kind
        self.fatal = fatal
        self.retryable = retryable
        self.retry_after = retry_after
        self.usage = usage or {}


class Ledger:
    """Spend and call counts per Vietnam day, persisted as JSON.

    load/save are injected (the bot passes its own load_json/save_json) so the
    file gets the same atomic write as state.json.
    """

    def __init__(self, path: Path, load, save):
        self.path = path
        self._save = save
        raw = load(path, {}) or {}
        self.data = {
            "days": raw.get("days", {}),
            "subscriptionCalls": raw.get("subscriptionCalls", {}),
            "apiDisabledUntil": float(raw.get("apiDisabledUntil", 0) or 0),
            "lastApiError": raw.get("lastApiError", ""),
            "subscriptionPausedUntil": float(raw.get("subscriptionPausedUntil", 0) or 0),
            "lastSubscriptionError": raw.get("lastSubscriptionError", ""),
        }

    def day(self, now: float) -> dict:
        return self.data["days"].setdefault(vietnam_day(now), {
            "spendUsd": 0.0, "hotSpendUsd": 0.0, "calls": 0, "byTier": {}, "errors": {}})

    def record_call(self, tier: str, cost: float, hot: bool, now: float) -> None:
        day = self.day(now)
        day["spendUsd"] = round(day["spendUsd"] + cost, 6)
        if hot:
            day["hotSpendUsd"] = round(day["hotSpendUsd"] + cost, 6)
        day["calls"] += 1
        day["byTier"][tier] = day["byTier"].get(tier, 0) + 1

    def record_error(self, kind: str, now: float) -> None:
        errors = self.day(now)["errors"]
        errors[kind] = errors.get(kind, 0) + 1

    def api_available(self, now: float) -> bool:
        return now >= self.data["apiDisabledUntil"]

    def disable_api(self, reason: str, now: float) -> None:
        self.data["apiDisabledUntil"] = now + API_OFF_SECONDS
        self.data["lastApiError"] = reason

    def subscription_calls(self, now: float) -> int:
        return int(self.data["subscriptionCalls"].get(vietnam_hour(now), 0))

    def record_subscription_call(self, now: float) -> None:
        self.data["subscriptionCalls"][vietnam_hour(now)] = self.subscription_calls(now) + 1

    def subscription_paused(self, now: float) -> bool:
        return now < self.data["subscriptionPausedUntil"]

    def pause_subscription(self, reason: str, now: float, seconds: float) -> None:
        self.data["subscriptionPausedUntil"] = now + seconds
        self.data["lastSubscriptionError"] = reason

    def save(self) -> None:
        for bucket, keep in (("days", KEEP_DAYS), ("subscriptionCalls", KEEP_HOURS)):
            entries = self.data[bucket]
            for key in sorted(entries)[:-keep]:
                del entries[key]
        self._save(self.path, self.data)


def cost_usd(usage: dict, model: str, prices: dict) -> float:
    price = prices.get(model) or FALLBACK_PRICE
    return (int(usage.get("input_tokens", 0) or 0) * float(price["input"])
            + int(usage.get("output_tokens", 0) or 0) * float(price["output"])) / 1_000_000


def _http_error(exc: urllib.error.HTTPError) -> ApiError:
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace")).get("error") or {}
    except Exception:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    # Only the machine code is kept: OpenAI echoes a masked key into "message".
    code = str(detail.get("code") or detail.get("type") or f"http-{exc.code}")
    if exc.code in (401, 403) or code in FATAL_ERROR_CODES:
        return ApiError(code, fatal=True)
    if exc.code == 429 or exc.code >= 500:
        try:
            retry_after = float((exc.headers or {}).get("Retry-After") or 0)
        except (TypeError, ValueError):
            retry_after = 0.0
        return ApiError(code, retryable=True, retry_after=retry_after)
    return ApiError(code)


def openai_request(prompt: str, model: str, cfg: dict, api_key: str) -> tuple[dict, dict]:
    """One Responses API call. Returns (reply, usage) or raises ApiError."""
    body = {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": cfg.get("reasoningEffort", "low")},
        "text": {"format": {"type": "json_schema", "name": "editorial",
                            "schema": EDITORIAL_SCHEMA, "strict": True}},
        "max_output_tokens": int(cfg.get("maxOutputTokens", 2000)),
        "store": False,
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=int(cfg.get("timeoutSeconds", 45))) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    except Exception:
        raise ApiError("network", retryable=True) from None

    usage = payload.get("usage") or {}
    if payload.get("status") != "completed":
        reason = (payload.get("incomplete_details") or {}).get("reason") or payload.get("status") or "unknown"
        raise ApiError(f"incomplete-{reason}", usage=usage)
    text = ""
    for part in payload.get("output") or []:
        if part.get("type") != "message":
            continue
        for content in part.get("content") or []:
            if content.get("type") == "refusal":
                raise ApiError("refusal", usage=usage)
            if content.get("type") == "output_text":
                text += content.get("text", "")
    try:
        reply = json.loads(text)
    except ValueError:
        raise ApiError("bad-json", usage=usage) from None
    if not isinstance(reply, dict):
        raise ApiError("bad-json", usage=usage)
    return reply, usage


def openai_editorial(prompt: str, model: str, cfg: dict, api_key: str, on_usage, sleep=None) -> dict:
    """Call the API with at most one retry. on_usage sees every billed attempt."""
    sleep = sleep or time.sleep
    for attempt in (0, 1):
        try:
            reply, usage = openai_request(prompt, model, cfg, api_key)
        except ApiError as err:
            if err.usage:
                on_usage(err.usage)
            if attempt == 0 and err.retryable:
                sleep(min(err.retry_after or 2.0, 10.0))
                continue
            raise
        on_usage(usage)
        return reply
    raise ApiError("unreachable")


def subscription_block_reason(cfg: dict, ledger: Ledger, quota: dict, now: float) -> str:
    """'' when the subscription tier may be used, otherwise why it may not."""
    if not cfg.get("enabled"):
        return "disabled"
    if ledger.subscription_paused(now):
        return "paused"
    if not isinstance(quota, dict):
        return "quota-unknown"
    try:
        quota_age_seconds = now - float(quota.get("at", 0) or 0)
        usable_profiles = int(quota.get("usableProfiles", 0) or 0)
    except (TypeError, ValueError):
        return "quota-unknown"
    if quota_age_seconds > float(cfg.get("quotaCacheMaxAgeMinutes", 120)) * 60:
        return "quota-unknown"
    if usable_profiles < int(cfg.get("minUsableProfiles", 2)):
        return "too-few-accounts"
    if ledger.subscription_calls(now) >= int(cfg.get("maxCallsPerHour", 10)):
        return "hourly-cap"
    return ""


def subscription_editorial(prompt: str, cfg: dict, cli: list[str]) -> str:
    """Run one isolated `openclaw agent exec` turn. Returns stdout, '' on failure.

    `agent exec` rather than `agent --session-key`: a fixed session key made
    every call resend the whole conversation (122k tokens by 2026-09-25).
    """
    timeout = int(cfg.get("timeoutSeconds", 180))
    with tempfile.TemporaryDirectory(prefix="fastnews-sub-") as workdir:
        prompt_path = Path(workdir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        command = [*cli, "agent", "exec", "--message-file", str(prompt_path),
                   "--model", str(cfg.get("model", "openai/gpt-5.5")),
                   "--thinking", "low", "--json", "--timeout", str(timeout),
                   "--cwd", workdir]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout + 30)
        except (subprocess.TimeoutExpired, OSError):
            return ""
    return result.stdout or ""
