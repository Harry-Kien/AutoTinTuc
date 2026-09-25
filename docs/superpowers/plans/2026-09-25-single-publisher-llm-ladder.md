# Single Publisher + LLM Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Python bot the only publisher to @fastnews247vn, writing every post with the paid OpenAI API first (gpt-5.5 for 5-star items, gpt-5.4-mini otherwise), falling back to a guarded subscription call and then machine translation, under a hard daily spend cap.

**Architecture:** A new standard-library module `scripts/fastnews247_llm.py` holds the transports (OpenAI Responses API, `openclaw agent exec`) and the spend ledger; it knows nothing about news. `scripts/fastnews247_mvp.py` gains an `editorial.mode: "ladder"` that builds the prompt, picks the tier, and holds every tier's output to the existing fact gates. The same file gains a Telegram channel source, conditional GET with a feed cache, cost guards in `run_once`, and Telegram 429 handling. Deployment moves the VPS timer to 2 minutes and posting to the direct Bot API.

**Tech Stack:** Python 3.10+ standard library only (`urllib`, `html.parser`, `json`, `subprocess`), systemd user units, bash.

**Spec:** `docs/superpowers/specs/2026-09-25-single-publisher-llm-ladder-design.md`

## Global Constraints

- Standard library only. No `requirements.txt`, no `pip install` (README promise).
- Every LLM tier is held to the same gates as translation: `_fact_numbers` subset of the source, `looks_vietnamese`, `headline_quality_issues`, `summary_quality_issues`. No tier may loosen them.
- Secrets never appear on a command line, in logs, or in error strings: the OpenAI key goes only in the `Authorization` header; the Telegram token is redacted with `_redact_token`.
- Budget values, verbatim from the spec: `dailyBudgetUsd` 3.0, `hotDailyBudgetUsd` 1.5, `hotMinScore` 5, `model` `gpt-5.4-mini`, `hotModel` `gpt-5.5`, prices per 1M tokens `gpt-5.4-mini` 0.75 in / 4.50 out, `gpt-5.5` 5.00 in / 30.00 out.
- Subscription guard, verbatim: quota cache at most 120 minutes old, `usableProfiles` ≥ 2, at most 10 calls per Vietnam hour.
- The Vietnam day and hour (UTC+7) are the budget and rate windows.
- Tests follow the repo's style: offline, one file per concern under `scripts/test_*.py`, runnable as `python scripts/test_x.py`, exit 0 on pass. Stub network and subprocess; never call the real API or Telegram.
- Keep `translate`, `auto`, `openclaw` modes and the `bridge` transport working. Windows and rollback use them.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

Run the full suite with:

```bash
for t in scripts/test_*.py; do python "$t" >/dev/null 2>&1 && echo "ok   $t" || echo "FAIL $t"; done
```

All 9 existing tests pass on commit `4e9f76e`; every task must keep them passing.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/fastnews247_llm.py` (new) | OpenAI Responses call, error classification, retry, cost, `Ledger`, subscription guard and `agent exec` call. No news logic. |
| `scripts/fastnews247_mvp.py` (modify) | `ladder` mode, prompt/validation helpers, Telegram channel parser, feed cache, `run_once` guards, Telegram 429 retry. |
| `config/fastnews247.sources.json` (modify) | Ladder config, direct transport, `maxPostsPerRun` 6, `rejectRetryMinutes` 60, Coin369 feed. |
| `deploy/fastnews247.timer` (modify) | 2-minute cadence. |
| `deploy/healthcheck.sh` (modify) | Bot API alerts, 3-hour quiet limit, ledger checks, subscription cache file. |
| `README.md`, `.env.example` (modify) | Document ladder mode and `OPENAI_API_KEY`. |
| `scripts/test_llm_transport.py` (new) | Tests for `fastnews247_llm.py`. |
| `scripts/test_editorial_ladder.py` (new) | Tests for ladder routing, budgets, gates. |
| `scripts/test_telegram_public.py` (new) | Tests for the channel parser and inline articles. |
| `scripts/test_feed_cache.py` (new) | Tests for conditional GET and the 304 path. |
| `scripts/test_run_once_guards.py` (new) | Tests for rejection memory, per-source ordering, Vietnamese dedupe. |
| `scripts/test_config_ladder.py` (new) | Guards the shipped config. |
| `scripts/test_telegram_direct.py` (modify) | Adds 429 cases. |

---

### Task 1: LLM transport module

**Files:**
- Create: `scripts/fastnews247_llm.py`
- Test: `scripts/test_llm_transport.py`

**Interfaces:**
- Consumes: nothing from the bot.
- Produces:
  - `class ApiError(Exception)` with attributes `kind: str`, `fatal: bool`, `retryable: bool`, `retry_after: float`, `usage: dict`
  - `class Ledger(path: Path, load, save)` with methods `day(now) -> dict`, `record_call(tier: str, cost: float, hot: bool, now: float)`, `record_error(kind: str, now: float)`, `api_available(now) -> bool`, `disable_api(reason: str, now: float)`, `subscription_calls(now) -> int`, `record_subscription_call(now)`, `save()`. `day()` returns `{"spendUsd", "hotSpendUsd", "calls", "byTier", "errors"}`.
  - `cost_usd(usage: dict, model: str, prices: dict) -> float`
  - `openai_request(prompt: str, model: str, cfg: dict, api_key: str) -> tuple[dict, dict]`
  - `openai_editorial(prompt, model, cfg, api_key, on_usage, sleep=None) -> dict`
  - `subscription_block_reason(cfg: dict, ledger: Ledger, quota: dict, now: float) -> str`
  - `subscription_editorial(prompt: str, cfg: dict, cli: list[str]) -> str`
  - Constants `API_OFF_SECONDS = 1800`, `OPENAI_RESPONSES_URL`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_llm_transport.py`:

```python
"""Offline tests for the paid-API and subscription transports and the ledger.

No network, no subprocess: urllib.request.urlopen and subprocess.run are
stubbed. The contract: every billed attempt is counted, keys never leak,
dead keys switch the API off instead of being retried, and the subscription
is only touched when its guard allows it.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import types
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_llm as llm  # noqa: E402

KEY = "sk-test-SECRETSECRETSECRET"
CFG = {"reasoningEffort": "low", "timeoutSeconds": 5, "maxOutputTokens": 800}
PRICES = {"gpt-5.4-mini": {"input": 0.75, "output": 4.50},
          "gpt-5.5": {"input": 5.00, "output": 30.00}}
NOW = 1790308000.0  # 2026-09-25 10:46 +07
failures: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def completed(text: str, usage=None) -> dict:
    return {"status": "completed",
            "output": [{"type": "reasoning", "summary": []},
                       {"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": usage or {"input_tokens": 2000, "output_tokens": 500}}


def http_error(code: int, body: dict, headers=None):
    return urllib.error.HTTPError(llm.OPENAI_RESPONSES_URL, code, "err", headers or {},
                                  io.BytesIO(json.dumps(body).encode("utf-8")))


def opener(*steps):
    """urlopen stand-in that plays steps in order: dict -> 200 JSON, Exception -> raised."""
    def _open(request, timeout=None):
        _open.requests.append(request)
        step = steps[len(_open.requests) - 1]
        if isinstance(step, Exception):
            raise step
        return _Response(json.dumps(step).encode("utf-8"))
    _open.requests = []
    return _open


def main() -> int:
    real_urlopen = urllib.request.urlopen
    real_run = llm.subprocess.run
    reply = json.dumps({"title": "Vàng tăng", "summary": "Vàng tăng mạnh."})
    try:
        print("cost_usd")
        usage = {"input_tokens": 2000, "output_tokens": 500}
        check("gpt-5.4-mini 2000/500", abs(llm.cost_usd(usage, "gpt-5.4-mini", PRICES) - 0.00375) < 1e-9)
        check("gpt-5.5 2000/500", abs(llm.cost_usd(usage, "gpt-5.5", PRICES) - 0.025) < 1e-9)
        check("unpriced model is charged the fallback (never free)",
              llm.cost_usd(usage, "gpt-unknown", PRICES) >= 0.025)

        print("openai_request")
        urllib.request.urlopen = opener(completed(reply))
        out, used = llm.openai_request("PROMPT", "gpt-5.4-mini", CFG, KEY)
        sent = urllib.request.urlopen.requests[0]
        body = json.loads(sent.data.decode("utf-8"))
        check("reply parsed from output_text", out == {"title": "Vàng tăng", "summary": "Vàng tăng mạnh."}, out)
        check("usage returned", used.get("output_tokens") == 500, used)
        check("posts to responses endpoint", sent.full_url == llm.OPENAI_RESPONSES_URL, sent.full_url)
        check("model + prompt in body", body["model"] == "gpt-5.4-mini" and body["input"] == "PROMPT", body)
        check("strict json schema", body["text"]["format"]["type"] == "json_schema"
              and body["text"]["format"]["strict"] is True, body["text"])
        check("reasoning effort from cfg", body["reasoning"] == {"effort": "low"}, body.get("reasoning"))
        check("store disabled", body["store"] is False, body.get("store"))
        check("key only in Authorization header",
              sent.get_header("Authorization") == f"Bearer {KEY}" and KEY not in sent.data.decode("utf-8"))

        urllib.request.urlopen = opener({"status": "incomplete",
                                         "incomplete_details": {"reason": "max_output_tokens"},
                                         "output": [], "usage": {"input_tokens": 10, "output_tokens": 800}})
        try:
            llm.openai_request("P", "gpt-5.4-mini", CFG, KEY)
            check("incomplete raises", False)
        except llm.ApiError as err:
            check("incomplete raises with kind", err.kind == "incomplete-max_output_tokens", err.kind)
            check("incomplete still reports billed usage", err.usage.get("output_tokens") == 800, err.usage)

        urllib.request.urlopen = opener({"status": "completed", "usage": {}, "output": [
            {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]})
        try:
            llm.openai_request("P", "gpt-5.4-mini", CFG, KEY)
            check("refusal raises", False)
        except llm.ApiError as err:
            check("refusal raises", err.kind == "refusal" and not err.fatal, err.kind)

        print("error classification")
        cases = [
            (http_error(401, {"error": {"code": "invalid_api_key", "message": f"bad {KEY}"}}), True, False),
            (http_error(429, {"error": {"code": "insufficient_quota"}}), True, False),
            (http_error(429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "3"}), False, True),
            (http_error(503, {"error": {"type": "server_error"}}), False, True),
            (http_error(400, {"error": {"code": "invalid_request"}}), False, False),
        ]
        for error, fatal, retryable in cases:
            urllib.request.urlopen = opener(error)
            try:
                llm.openai_request("P", "gpt-5.4-mini", CFG, KEY)
                check(f"HTTP {error.code} raises", False)
            except llm.ApiError as err:
                check(f"HTTP {error.code} fatal={fatal} retryable={retryable}",
                      err.fatal == fatal and err.retryable == retryable, (err.kind, err.fatal, err.retryable))
                check(f"HTTP {error.code} kind never contains the key", KEY not in err.kind, err.kind)
        urllib.request.urlopen = opener(http_error(429, {"error": {"code": "rate_limit_exceeded"}},
                                                   {"Retry-After": "3"}))
        try:
            llm.openai_request("P", "gpt-5.4-mini", CFG, KEY)
        except llm.ApiError as err:
            check("Retry-After read", err.retry_after == 3.0, err.retry_after)
        urllib.request.urlopen = opener(TimeoutError("slow"))
        try:
            llm.openai_request("P", "gpt-5.4-mini", CFG, KEY)
        except llm.ApiError as err:
            check("network error is retryable", err.kind == "network" and err.retryable, err.kind)

        print("openai_editorial retry policy")
        billed, slept = [], []
        urllib.request.urlopen = opener(
            http_error(429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "3"}), completed(reply))
        out = llm.openai_editorial("P", "gpt-5.4-mini", CFG, KEY, billed.append, sleep=slept.append)
        check("429 then success -> reply", out.get("title") == "Vàng tăng", out)
        check("slept Retry-After once", slept == [3.0], slept)
        check("billed once", len(billed) == 1, billed)

        slept.clear()
        urllib.request.urlopen = opener(http_error(503, {}), http_error(503, {}), completed(reply))
        try:
            llm.openai_editorial("P", "gpt-5.4-mini", CFG, KEY, billed.append, sleep=slept.append)
            check("two 503s raise", False)
        except llm.ApiError:
            check("two 503s raise after exactly 2 attempts", len(urllib.request.urlopen.requests) == 2)
        check("retry wait defaults to 2s", slept == [2.0], slept)

        slept.clear()
        urllib.request.urlopen = opener(http_error(401, {"error": {"code": "invalid_api_key"}}), completed(reply))
        try:
            llm.openai_editorial("P", "gpt-5.4-mini", CFG, KEY, billed.append, sleep=slept.append)
        except llm.ApiError as err:
            check("401 is not retried", len(urllib.request.urlopen.requests) == 1 and not slept and err.fatal)

        billed.clear()
        urllib.request.urlopen = opener({"status": "incomplete", "incomplete_details": {"reason": "x"},
                                         "output": [], "usage": {"input_tokens": 5, "output_tokens": 7}})
        try:
            llm.openai_editorial("P", "gpt-5.4-mini", CFG, KEY, billed.append, sleep=slept.append)
        except llm.ApiError:
            check("failed-but-billed attempt is counted", billed == [{"input_tokens": 5, "output_tokens": 7}], billed)

        print("Ledger")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            load = lambda p, default: json.loads(p.read_text(encoding="utf-8")) if p.exists() else default  # noqa: E731
            save = lambda p, data: p.write_text(json.dumps(data), encoding="utf-8")  # noqa: E731
            ledger = llm.Ledger(path, load, save)
            ledger.record_call("openai-hot", 0.025, True, NOW)
            ledger.record_call("openai", 0.00375, False, NOW)
            day = ledger.day(NOW)
            check("day keyed in Vietnam time", "2026-09-25" in ledger.data["days"], list(ledger.data["days"]))
            check("spend sums both", abs(day["spendUsd"] - 0.02875) < 1e-9, day)
            check("hot spend only hot", abs(day["hotSpendUsd"] - 0.025) < 1e-9, day)
            check("calls + byTier", day["calls"] == 2 and day["byTier"] == {"openai-hot": 1, "openai": 1}, day)
            ledger.record_error("network", NOW)
            ledger.record_error("network", NOW)
            check("errors counted", ledger.day(NOW)["errors"] == {"network": 2}, ledger.day(NOW)["errors"])

            check("api available by default", ledger.api_available(NOW))
            ledger.disable_api("invalid_api_key", NOW)
            check("disabled right after", not ledger.api_available(NOW + 60))
            check("back after API_OFF_SECONDS", ledger.api_available(NOW + llm.API_OFF_SECONDS))
            check("reason kept", ledger.data["lastApiError"] == "invalid_api_key")

            check("no subscription calls yet", ledger.subscription_calls(NOW) == 0)
            ledger.record_subscription_call(NOW)
            ledger.record_subscription_call(NOW + 60)
            check("counted per Vietnam hour", ledger.subscription_calls(NOW) == 2)
            check("new hour starts at zero", ledger.subscription_calls(NOW + 3600) == 0)

            for offset in range(20):
                ledger.record_call("openai", 0.001, False, NOW - offset * 86400)
            ledger.save()
            reloaded = llm.Ledger(path, load, save)
            check("persisted and pruned to 14 days", len(reloaded.data["days"]) == 14, len(reloaded.data["days"]))
            check("apiDisabledUntil persisted", reloaded.data["apiDisabledUntil"] == NOW + llm.API_OFF_SECONDS)

            print("subscription_block_reason")
            sub = {"enabled": True, "maxCallsPerHour": 2, "minUsableProfiles": 2, "quotaCacheMaxAgeMinutes": 120}
            fresh = {"at": NOW - 600, "usableProfiles": 3}
            empty = llm.Ledger(Path(directory) / "other.json", load, save)
            check("allowed", llm.subscription_block_reason(sub, empty, fresh, NOW) == "")
            check("disabled", llm.subscription_block_reason(dict(sub, enabled=False), empty, fresh, NOW) == "disabled")
            check("missing quota file", llm.subscription_block_reason(sub, empty, {}, NOW) == "quota-unknown")
            check("stale quota", llm.subscription_block_reason(sub, empty, {"at": NOW - 7300, "usableProfiles": 5}, NOW)
                  == "quota-unknown")
            check("too few accounts", llm.subscription_block_reason(sub, empty, {"at": NOW, "usableProfiles": 1}, NOW)
                  == "too-few-accounts")
            empty.record_subscription_call(NOW)
            empty.record_subscription_call(NOW)
            check("hourly cap", llm.subscription_block_reason(sub, empty, fresh, NOW) == "hourly-cap")

        print("subscription_editorial")
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = list(command)
            index = command.index("--message-file")
            seen["prompt"] = Path(command[index + 1]).read_text(encoding="utf-8")
            return types.SimpleNamespace(stdout='{"text": "ok"}', stderr="", returncode=0)

        llm.subprocess.run = fake_run
        out = llm.subscription_editorial("ARTICLE BODY", {"model": "openai/gpt-5.5", "timeoutSeconds": 60},
                                         ["node", "/fake/openclaw.mjs"])
        command = seen["command"]
        check("returns stdout", out == '{"text": "ok"}', out)
        check("isolated agent exec", command[2:4] == ["agent", "exec"], command)
        check("no persistent session", "--session-key" not in command, command)
        check("model passed", command[command.index("--model") + 1] == "openai/gpt-5.5", command)
        check("prompt via file, not argv", seen["prompt"] == "ARTICLE BODY"
              and not any("ARTICLE BODY" in part for part in command), command)

        def slow_run(command, **kwargs):
            raise llm.subprocess.TimeoutExpired(command, 1)

        llm.subprocess.run = slow_run
        check("timeout -> empty", llm.subscription_editorial("P", {}, ["openclaw"]) == "")
    finally:
        urllib.request.urlopen = real_urlopen
        llm.subprocess.run = real_run

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All LLM transport tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_llm_transport.py`
Expected: `ModuleNotFoundError: No module named 'fastnews247_llm'`

- [ ] **Step 3: Write the module**

Create `scripts/fastnews247_llm.py`:

```python
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
    if now - float(quota.get("at", 0) or 0) > float(cfg.get("quotaCacheMaxAgeMinutes", 120)) * 60:
        return "quota-unknown"
    if int(quota.get("usableProfiles", 0) or 0) < int(cfg.get("minUsableProfiles", 2)):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python scripts/test_llm_transport.py`
Expected: last line `All LLM transport tests passed.`, exit 0.

- [ ] **Step 5: Run the full suite, then commit**

Run the full-suite loop from Global Constraints. Expected: all `ok`.

```bash
git add scripts/fastnews247_llm.py scripts/test_llm_transport.py
git commit -m "Add the OpenAI and subscription transports behind a spend ledger

Standard library only. Dead keys (401/403/insufficient_quota) switch the API
off for 30 minutes instead of being retried; 429/5xx get one retry; every
billed attempt reaches the ledger, including incomplete responses.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Ladder editorial mode

**Files:**
- Modify: `scripts/fastnews247_mvp.py`: imports (after line 30), new constants after `CONFIG_PATH` (line 39), `openclaw_rewrite` (lines 965-1025), `editorial_for` (lines 1028-1069)
- Test: `scripts/test_editorial_ladder.py`

**Interfaces:**
- Consumes (Task 1): `llm.Ledger`, `llm.cost_usd`, `llm.openai_editorial`, `llm.ApiError`, `llm.subscription_block_reason`, `llm.subscription_editorial`
- Produces:
  - `LEDGER_PATH = Path("storage/fastnews247/llm_ledger.json")`
  - `QUOTA_PATH = Path("storage/fastnews247/subscription_quota.json")`
  - `_editorial_prompt(item: dict, body_chars: int = 0) -> tuple[str, str, str]` returns `(prompt, source_title, source_body)`
  - `_validated_rewrite(payload: dict, source_title: str, source_body: str) -> tuple[str, str]`
  - `ladder_rewrite(item: dict, config: dict, now: float | None = None) -> tuple[str, str]`, which sets `item["editorial_path"]` to one of `openai-hot`, `openai`, `subscription`, `translate`, plus `item["editorial_model"]` on API tiers
  - `editorial_for(item, config, budget)` dispatches `mode == "ladder"` to `ladder_rewrite`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_editorial_ladder.py`:

```python
"""Offline tests for editorial.mode "ladder".

The transports are stubbed on the llm module; the ledger is real and lives in
a temporary ROOT. The contract: hot items get the strong model until its own
budget runs out, the daily cap stops paid calls, dead keys stop being called,
a model that invents facts falls to translation rather than another LLM, and
the subscription is used only when the API cannot be and its guard allows.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_llm as llm  # noqa: E402
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
KEY_ENV = "TEST_LADDER_OPENAI_KEY"
GOOD = {"title": "Vàng tăng 2,5% khi Fed giữ nguyên lãi suất",
        "summary": "Vàng tăng 2,5% hôm thứ Ba sau khi Fed giữ nguyên lãi suất, theo giới phân tích."}
TRANSLATED = ("Vàng tăng 2,5% sau quyết định của Fed", "Bản dịch máy của tin vàng hôm thứ Ba.")
CONFIG = {"editorial": {
    "mode": "ladder",
    "openai": {"apiKeyEnv": KEY_ENV, "model": "gpt-5.4-mini", "hotModel": "gpt-5.5", "hotMinScore": 5,
               "dailyBudgetUsd": 3.0, "hotDailyBudgetUsd": 1.5, "sourceChars": 3000,
               "pricesPerMTok": {"gpt-5.4-mini": {"input": 0.75, "output": 4.50},
                                 "gpt-5.5": {"input": 5.00, "output": 30.00}}},
    "subscription": {"enabled": True, "model": "openai/gpt-5.5", "maxCallsPerHour": 1,
                     "minUsableProfiles": 2, "quotaCacheMaxAgeMinutes": 120}}}


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def item(score: int) -> dict:
    return {"title": "Gold climbs 2.5% as the Fed holds rates", "score": score,
            "article_text": "Gold climbed 2.5% on Tuesday after the Fed held rates steady, analysts said. "
                            "Traders now watch the next inflation report."}


class FakeApi:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, prompt, model, cfg, api_key, on_usage, sleep=None):
        self.calls.append({"prompt": prompt, "model": model, "key": api_key})
        if self.error:
            raise self.error
        on_usage({"input_tokens": 2000, "output_tokens": 500})
        return self.reply


class FakeSubscription:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def __call__(self, prompt, cfg, cli):
        self.calls += 1
        return json.dumps({"text": json.dumps(self.reply)})


def ledger_day(root: Path) -> dict:
    data = json.loads((root / bot.LEDGER_PATH).read_text(encoding="utf-8"))
    return data["days"][llm.vietnam_day(time.time())]


def main() -> int:
    saved = (llm.openai_editorial, llm.subscription_editorial, bot.vietnamese_editorial,
             bot.resolve_openclaw_cli, bot.ROOT)
    os.environ[KEY_ENV] = "sk-test-ladder"
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bot.ROOT = root
            bot.vietnamese_editorial = lambda it: TRANSLATED
            bot.resolve_openclaw_cli = lambda: ["node", "/fake/openclaw.mjs"]
            sub = FakeSubscription(GOOD)
            llm.subscription_editorial = sub

            print("hot item -> strong model, counted as hot spend")
            api = FakeApi(GOOD)
            llm.openai_editorial = api
            news = item(5)
            title, summary = bot.editorial_for(news, CONFIG, {"remaining": 0})
            check("LLM text used", (title, summary) == (GOOD["title"], GOOD["summary"]), title)
            check("gpt-5.5 for 5 stars", api.calls[0]["model"] == "gpt-5.5", api.calls)
            check("path openai-hot", news.get("editorial_path") == "openai-hot", news.get("editorial_path"))
            day = ledger_day(root)
            check("spend 0.025", abs(day["spendUsd"] - 0.025) < 1e-9, day)
            check("hot spend 0.025", abs(day["hotSpendUsd"] - 0.025) < 1e-9, day)
            check("prompt carries article text, not just 2 sentences",
                  "next inflation report" in api.calls[0]["prompt"], api.calls[0]["prompt"][-200:])
            check("key never in prompt", "sk-test-ladder" not in api.calls[0]["prompt"])

            print("4-star item -> mini model")
            api = FakeApi(GOOD)
            llm.openai_editorial = api
            news = item(4)
            bot.editorial_for(news, CONFIG, None)
            check("gpt-5.4-mini for 4 stars", api.calls[0]["model"] == "gpt-5.4-mini", api.calls)
            check("path openai", news.get("editorial_path") == "openai", news.get("editorial_path"))

            print("hot budget spent -> hot item drops to mini")
            ledger_path = root / bot.LEDGER_PATH
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            data["days"][llm.vietnam_day(time.time())]["hotSpendUsd"] = 1.5
            ledger_path.write_text(json.dumps(data), encoding="utf-8")
            api = FakeApi(GOOD)
            llm.openai_editorial = api
            bot.editorial_for(item(5), CONFIG, None)
            check("mini once hot budget is gone", api.calls[0]["model"] == "gpt-5.4-mini", api.calls)

            print("daily cap reached -> no paid call; no quota file -> translation")
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            data["days"][llm.vietnam_day(time.time())]["spendUsd"] = 3.0
            ledger_path.write_text(json.dumps(data), encoding="utf-8")
            api = FakeApi(GOOD)
            llm.openai_editorial = api
            sub.calls = 0
            news = item(4)
            title, _ = bot.editorial_for(news, CONFIG, None)
            check("API not called over cap", api.calls == [], api.calls)
            check("subscription blocked without quota file", sub.calls == 0, sub.calls)
            check("translation used", title == TRANSLATED[0] and news["editorial_path"] == "translate", title)

            print("subscription used when API is unavailable and the guard allows")
            (root / bot.QUOTA_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / bot.QUOTA_PATH).write_text(json.dumps({"at": time.time(), "usableProfiles": 3}),
                                               encoding="utf-8")
            news = item(4)
            title, _ = bot.editorial_for(news, CONFIG, None)
            check("subscription called once", sub.calls == 1, sub.calls)
            check("subscription text used", title == GOOD["title"] and news["editorial_path"] == "subscription",
                  (title, news.get("editorial_path")))
            news = item(4)
            bot.editorial_for(news, CONFIG, None)
            check("hourly cap (1) respected", sub.calls == 1 and news["editorial_path"] == "translate", sub.calls)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bot.ROOT = root
            sub.calls = 0

            print("missing key -> recorded, no API call")
            os.environ[KEY_ENV] = ""
            api = FakeApi(GOOD)
            llm.openai_editorial = api
            news = item(4)
            bot.editorial_for(news, CONFIG, None)
            check("no call without key", api.calls == [], api.calls)
            check("no-api-key recorded", ledger_day(root)["errors"].get("no-api-key") == 1, ledger_day(root))
            os.environ[KEY_ENV] = "sk-test-ladder"

            print("dead key -> API switched off for 30 minutes")
            api = FakeApi(error=llm.ApiError("invalid_api_key", fatal=True))
            llm.openai_editorial = api
            bot.editorial_for(item(4), CONFIG, None)
            bot.editorial_for(item(4), CONFIG, None)
            check("dead key called only once", len(api.calls) == 1, len(api.calls))
            data = json.loads((root / bot.LEDGER_PATH).read_text(encoding="utf-8"))
            check("lastApiError kept", data["lastApiError"] == "invalid_api_key", data["lastApiError"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bot.ROOT = root
            (root / bot.QUOTA_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / bot.QUOTA_PATH).write_text(json.dumps({"at": time.time(), "usableProfiles": 3}),
                                               encoding="utf-8")
            sub.calls = 0

            print("invented number -> translation, NOT another LLM")
            api = FakeApi({"title": "Vàng tăng 7,9% khi Fed giữ nguyên lãi suất",
                           "summary": "Vàng tăng 7,9% hôm thứ Ba, theo giới phân tích."})
            llm.openai_editorial = api
            news = item(4)
            title, _ = bot.editorial_for(news, CONFIG, None)
            check("fact gate rejects", title == TRANSLATED[0] and news["editorial_path"] == "translate", title)
            check("subscription not tried after a gate failure", sub.calls == 0, sub.calls)
            check("gate-rejected recorded", ledger_day(root)["errors"].get("gate-rejected") == 1, ledger_day(root))

            print("transient API error -> subscription (guard allows)")
            api = FakeApi(error=llm.ApiError("rate_limit_exceeded", retryable=True))
            llm.openai_editorial = api
            news = item(4)
            title, _ = bot.editorial_for(news, CONFIG, None)
            check("fell to subscription", news["editorial_path"] == "subscription" and sub.calls == 1,
                  (news.get("editorial_path"), sub.calls))
            data = json.loads((root / bot.LEDGER_PATH).read_text(encoding="utf-8"))
            check("transient error does not disable API", data["apiDisabledUntil"] == 0, data["apiDisabledUntil"])

        print("other modes untouched")
        check("translate mode ignores ladder config",
              bot.editorial_for(item(4), {"editorial": {"mode": "translate"}}, None)[0] == TRANSLATED[0])
    finally:
        (llm.openai_editorial, llm.subscription_editorial, bot.vietnamese_editorial,
         bot.resolve_openclaw_cli, bot.ROOT) = saved
        os.environ.pop(KEY_ENV, None)

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All ladder tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_editorial_ladder.py`
Expected: `AttributeError: module 'fastnews247_mvp' has no attribute 'LEDGER_PATH'`

- [ ] **Step 3: Implement**

In `scripts/fastnews247_mvp.py`, after `from pathlib import Path` (line 30) add:

```python

import fastnews247_llm as llm
```

After `CONFIG_PATH = ...` (line 39) add:

```python
LEDGER_PATH = Path("storage/fastnews247/llm_ledger.json")
QUOTA_PATH = Path("storage/fastnews247/subscription_quota.json")
```

Replace the whole `openclaw_rewrite` function (lines 965-1025) with these three functions (the behaviour of `openclaw_rewrite` is unchanged, now through the shared helpers):

```python
def _editorial_prompt(item: dict, body_chars: int = 0) -> tuple[str, str, str]:
    """Return (prompt, source_title, source_body) for any LLM tier.

    body_chars > 0 hands the model up to that much verified article text
    instead of the two ranked sentences. The numbers it may use are then
    checked against exactly the text it was given.
    """
    source_title = strip_urls(item.get("title", ""))
    article = strip_urls(item.get("article_text", ""))
    if body_chars > 0 and article:
        source_body = article[:body_chars]
    else:
        sentences = item.get("source_sentences") or source_summary_sentences(item)
        source_body = " ".join(sentences)[:4000]
    prompt = EDITORIAL_PROMPT.replace("{source_title}", source_title).replace(
        "{source_body}", source_body)
    return prompt, source_title, source_body


def _validated_rewrite(payload: dict, source_title: str, source_body: str) -> tuple[str, str]:
    """The fact gates every LLM tier must pass; ("", "") when it fails any."""
    title = strip_urls(str(payload.get("title", ""))).strip().rstrip(" .")
    summary = strip_urls(str(payload.get("summary", ""))).strip()
    if not title or not summary:
        return "", ""
    if not looks_vietnamese(title) or not looks_vietnamese(summary):
        return "", ""
    allowed = _fact_numbers(source_title) | _fact_numbers(source_body)
    if not _fact_numbers(title).issubset(allowed):
        return "", ""
    if not _fact_numbers(summary).issubset(allowed):
        return "", ""
    return title, summary


def openclaw_rewrite(item: dict, cfg: dict) -> tuple[str, str]:
    """Ask the OpenClaw agent for a Vietnamese rewrite.

    Fails closed: any error returns ("", "") so the caller drops the item, which
    is exactly what the machine-translation path does when it cannot be trusted.
    The result is then held to the SAME fact checks as that path - the LLM is
    allowed to write better prose, never to introduce facts.
    """
    prompt, source_title, source_body = _editorial_prompt(item)
    if not source_title or not source_body:
        return "", ""

    try:
        cli = resolve_openclaw_cli()
    except RuntimeError:
        return "", ""

    handle, prompt_path = tempfile.mkstemp(suffix=".txt", prefix="fastnews-editorial-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(prompt)
        # --message-file keeps third-party article text off the command line.
        command = [*cli, "agent", "--message-file", prompt_path, "--json"]
        if cfg.get("model"):
            command += ["--model", str(cfg["model"])]
        if cfg.get("sessionKey"):
            command += ["--session-key", str(cfg["sessionKey"])]
        timeout = int(cfg.get("timeoutSeconds", 180))
        command += ["--timeout", str(timeout)]
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace",
                                    timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            return "", ""
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass

    payload = _extract_json_object(_extract_agent_text(result.stdout))
    return _validated_rewrite(payload, source_title, source_body)


def ladder_rewrite(item: dict, config: dict, now: float | None = None) -> tuple[str, str]:
    """editorial.mode "ladder": paid API, then subscription, then translation.

    Each LLM tier is held to _validated_rewrite here and to the headline and
    summary gates in draft_post, so paying for a model buys better prose, never
    looser checks. A model that answers with invented facts drops straight to
    translation rather than getting a second LLM opinion.
    """
    now = time.time() if now is None else now
    ledger = llm.Ledger(ROOT / LEDGER_PATH, load_json, save_json)
    try:
        title, summary = _ladder_llm(item, config.get("editorial", {}), ledger, now)
    finally:
        ledger.save()
    if title and summary:
        return title, summary
    item["editorial_path"] = "translate"
    return vietnamese_editorial(item)


def _ladder_llm(item: dict, cfg: dict, ledger: "llm.Ledger", now: float) -> tuple[str, str]:
    api = cfg.get("openai", {})
    prompt, source_title, source_body = _editorial_prompt(item, int(api.get("sourceChars", 3000)))
    if not source_title or not source_body:
        return "", ""

    key = os.environ.get(api.get("apiKeyEnv", "OPENAI_API_KEY"), "").strip()
    day = ledger.day(now)
    if not key:
        ledger.record_error("no-api-key", now)
    elif ledger.api_available(now) and day["spendUsd"] < float(api.get("dailyBudgetUsd", 0)):
        hot = (int(item.get("score", 0)) >= int(api.get("hotMinScore", 5))
               and day["hotSpendUsd"] < float(api.get("hotDailyBudgetUsd", 0)))
        model = str(api.get("hotModel") if hot else api.get("model"))
        tier = "openai-hot" if hot else "openai"
        prices = api.get("pricesPerMTok", {})
        try:
            reply = llm.openai_editorial(
                prompt, model, api, key,
                lambda usage: ledger.record_call(tier, llm.cost_usd(usage, model, prices), hot, now))
        except llm.ApiError as err:
            ledger.record_error(err.kind, now)
            if err.fatal:
                ledger.disable_api(err.kind, now)
        else:
            title, summary = _validated_rewrite(reply, source_title, source_body)
            if title and summary:
                item["editorial_path"] = tier
                item["editorial_model"] = model
                return title, summary
            ledger.record_error("gate-rejected", now)
            return "", ""

    sub = cfg.get("subscription", {})
    if llm.subscription_block_reason(sub, ledger, load_json(ROOT / QUOTA_PATH, {}) or {}, now):
        return "", ""
    try:
        cli = resolve_openclaw_cli()
    except RuntimeError:
        return "", ""
    ledger.record_subscription_call(now)
    stdout = llm.subscription_editorial(prompt, sub, cli)
    title, summary = _validated_rewrite(
        _extract_json_object(_extract_agent_text(stdout)), source_title, source_body)
    if title and summary:
        ledger.record_call("subscription", 0.0, False, now)
        item["editorial_path"] = "subscription"
        return title, summary
    ledger.record_error("subscription-failed", now)
    return "", ""
```

In `editorial_for`, extend the docstring's mode list with:

```
      "ladder"              - OpenAI API, then subscription, then translate;
                              capped by editorial.openai.dailyBudgetUsd
```

and insert as the first statements after `mode = cfg.get("mode", "translate")`:

```python
    if mode == "ladder":
        return ladder_rewrite(item, config or {})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python scripts/test_editorial_ladder.py`, then `python scripts/test_editorial_modes.py`, then `python scripts/test_editorial_retry.py`
Expected: `All ladder tests passed.`, `All editorial routing tests passed.`, `All editorial-retry tests passed.`

- [ ] **Step 5: Full suite, commit**

```bash
git add scripts/fastnews247_mvp.py scripts/test_editorial_ladder.py
git commit -m "Add editorial mode ladder: paid API, then subscription, then translation

5-star items use the strong model until their own daily budget is spent; a
daily cap stops paid calls; every tier passes the same fact gates. The model
now reads up to 3000 chars of the verified article instead of two sentences.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Telegram channel source (Coin369)

**Files:**
- Modify: `scripts/fastnews247_mvp.py`: `parse_feed` (lines 155-187), add parser after `parse_time`, and `run_once` in the article step (lines 1423-1446)
- Test: `scripts/test_telegram_public.py`

**Interfaces:**
- Produces:
  - `parse_rss_items(raw: bytes, feed: dict) -> list[dict]` (the old parse body)
  - `class TelegramChannelParser(HTMLParser)` with `.messages: list[dict]`, each `{"post", "text", "published"}`
  - `parse_telegram_channel(raw: bytes, feed: dict) -> list[dict]`: items carry `inline_article: str` and `minimumTextChars: int`
  - `parse_feed(feed)` dispatches on `feed.get("type") == "telegram_public"`
  - In `run_once`, an item with `inline_article` uses it as `article_text` without any fetch

- [ ] **Step 1: Write the failing test**

Create `scripts/test_telegram_public.py`:

```python
"""Offline tests for the public Telegram channel source (t.me/s/<channel>).

The fixture copies the markup t.me served for coin369channel on 2026-09-25.
The contract: each message becomes one item with its own link and timestamp,
the leading "🔹 10:35:" marker is not part of the text, messages without text
are skipped, and run_once uses the message itself as the article - it never
tries to fetch a page for it.
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
FEED = {"name": "Coin369", "type": "telegram_public", "url": "https://t.me/s/coin369channel",
        "category": "world_macro", "priority": 2, "sourceTier": "repost", "minimumTextChars": 80}


def message(post: str, stamp: str, inner: str) -> str:
    text = (f'<div class="tgme_widget_message_text js-message_text" dir="auto">{inner}</div>'
            if inner is not None else '<div class="tgme_widget_message_photo_wrap"></div>')
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="{post}" data-view="x">'
        '<div class="tgme_widget_message_bubble">'
        '<div class="tgme_widget_message_author accent_color"><a class="tgme_widget_message_owner_name" '
        'href="https://t.me/coin369channel"><span dir="auto">Tin nhanh - 369</span></a></div>'
        f'{text}'
        '<div class="tgme_widget_message_footer compact js-message_footer"><div class="tgme_widget_message_info">'
        '<span class="tgme_widget_message_views">45</span><span class="tgme_widget_message_meta">'
        f'<a class="tgme_widget_message_date" href="https://t.me/{post}"><time datetime="{stamp}" class="time">'
        '03:35</time></a></span></div></div></div></div></div>')


EMOJI = ('<i class="emoji" style="background-image:url(\'//telegram.org/img/emoji/40/F09F94B9.png\')">'
         '<b>🔹</b></i>')
PAGE = ("<html><body><section>"
        + message("coin369channel/354808", "2026-09-25T03:35:11+00:00",
                  f"{EMOJI}  10:35:  Lợi suất trái phiếu chính phủ chuẩn kỳ hạn 10 năm của Ấn Độ là 7,1347%; "
                  "tăng lên mức cao nhất kể từ ngày 20 tháng 5.")
        + message("coin369channel/354809", "2026-09-25T03:44:12+00:00", None)
        + message("coin369channel/354810", "2026-09-25T03:47:12+00:00",
                  f"{EMOJI} 10:47: Thủ tướng &quot;Netanyahu&quot; phát biểu tại Liên hợp quốc.<br/>"
                  "Đa số đại diện các quốc gia đã rời khỏi hội trường để phản đối.")
        + "</section></body></html>").encode("utf-8")


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    print("parser")
    items = bot.parse_telegram_channel(PAGE, FEED)
    check("two text messages, photo-only skipped", len(items) == 2, len(items))
    first, second = items
    check("marker stripped from title", first["title"].startswith("Lợi suất trái phiếu"), first["title"])
    check("title is the first sentence", first["title"].endswith("20 tháng 5."), first["title"])
    check("link per message", first["link"] == "https://t.me/coin369channel/354808", first["link"])
    check("timestamp kept and parseable", bot.parse_time(first["published"]) > 0, first["published"])
    check("inline article is the text", first["inline_article"].startswith("Lợi suất"), first["inline_article"])
    check("feed metadata copied", first["source"] == "Coin369" and first["priority"] == 2
          and first["minimumTextChars"] == 80, first)
    check("entities decoded", '"Netanyahu"' in second["title"], second["title"])
    check("<br> splits title from body", second["title"].endswith("Liên hợp quốc."), second["title"])
    check("body keeps both lines", "rời khỏi hội trường" in second["inline_article"], second["inline_article"])
    check("title has no emoji marker", "🔹" not in second["title"] and "10:47" not in second["title"],
          second["title"])

    print("parse_feed dispatches on type")
    with patch.object(bot, "fetch_url", return_value=PAGE):
        check("telegram_public routed to channel parser", len(bot.parse_feed(FEED)) == 2)

    print("run_once uses the inline article and never fetches a page")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    fresh = dict(first, published=now)
    thin = dict(second, published=now, title="Tin ngắn khác hẳn", inline_article="Quá ngắn.",
                link="https://t.me/coin369channel/1")
    seen_articles = []

    def fake_draft(item, *args):
        seen_articles.append(item.get("article_text", ""))
        return "draft", []

    config = {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
              "posting": {"telegram": {"mode": "direct", "tokenEnv": "X", "channelId": "@x"},
                          "duplicateWindowHours": 72, "minimumScoreToDraft": 4, "minimumScoreToPost": 4,
                          "maxPostsPerRun": 5, "requireSourceArticle": True},
              "articleContent": {"preferHtml": True}, "feeds": [FEED]}
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(bot, "ROOT", Path(directory)), \
            patch.object(bot, "parse_feed", return_value=[fresh, thin]), \
            patch.object(bot, "score_item", return_value=(4, [], "fixture")), \
            patch.object(bot, "source_article_text", side_effect=AssertionError("must not fetch")), \
            patch.object(bot, "draft_post", side_effect=fake_draft):
        bot.run_once(config, post=False)
        report = (Path(directory) / "draft.md").read_text(encoding="utf-8")
    check("drafted from the message text", seen_articles and seen_articles[0].startswith("Lợi suất"),
          seen_articles)
    check("too-thin message rejected", len(seen_articles) == 1 and "article-text-too-thin" in report, report[-400:])

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All Telegram channel tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_telegram_public.py`
Expected: `AttributeError: module 'fastnews247_mvp' has no attribute 'parse_telegram_channel'`

- [ ] **Step 3: Implement**

Replace `parse_feed` (lines 155-187) with:

```python
def parse_feed(feed: dict) -> list[dict]:
    raw = fetch_url(feed["url"], timeout=int(feed.get("timeoutSeconds", 10)))
    if feed.get("type") == "telegram_public":
        return parse_telegram_channel(raw, feed)
    return parse_rss_items(raw, feed)


def parse_rss_items(raw: bytes, feed: dict) -> list[dict]:
    root = ET.fromstring(raw)
    items = [node for node in root.iter() if node.tag.split("}")[-1] == "item"]
    if not items:
        items = root.findall("{http://www.w3.org/2005/Atom}entry")

    parsed = []
    for item in items[:30]:
        title = strip_html(child_text(item, ["title", "{http://www.w3.org/2005/Atom}title"]))
        summary = strip_html(child_text(item, ["description", "summary", "{http://www.w3.org/2005/Atom}summary"]))
        link = child_text(item, ["link", "guid", "{http://www.w3.org/2005/Atom}id"])
        atom_link = item.find("{http://www.w3.org/2005/Atom}link")
        if atom_link is not None and atom_link.attrib.get("href"):
            link = atom_link.attrib["href"]
        published = child_text(item, ["pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated", "{http://purl.org/dc/elements/1.1/}date"])

        if title:
            parsed.append(
                {
                    "title": title,
                    "summary": summary,
                    "link": strip_html(link),
                    "published": published,
                    "source": feed["name"],
                    "category": feed.get("category", "general"),
                    "priority": int(feed.get("priority", 3)),
                    "region": feed.get("region", "global"),
                    "sourceTier": feed.get("sourceTier", "secondary"),
                    "articleHtmlAllowed": feed.get("articleHtmlAllowed", True),
                }
            )
    return parsed
```

After `parse_time` (ends line 202), add:

```python
TELEGRAM_TIME_MARKER = re.compile(r"^\W*\d{1,2}:\d{2}\s*:\s*")


class TelegramChannelParser(HTMLParser):
    """Messages on a public channel preview page (https://t.me/s/<channel>)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict] = []
        self._current: dict | None = None
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "tgme_widget_message" in classes and attributes.get("data-post"):
            self._current = {"post": attributes["data-post"], "text": "", "published": ""}
            self.messages.append(self._current)
            return
        if self._current is None:
            return
        if self._depth:
            if tag == "div":
                self._depth += 1
            elif tag == "br":
                self._parts.append("\n")
        elif tag == "div" and "tgme_widget_message_text" in classes and not self._current["text"]:
            self._depth = 1
            self._parts = []
        if tag == "time" and attributes.get("datetime") and not self._current["published"]:
            self._current["published"] = attributes["datetime"]

    def handle_endtag(self, tag):
        if self._depth and tag == "div":
            self._depth -= 1
            if self._depth == 0 and self._current is not None:
                self._current["text"] = "".join(self._parts)

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)


def parse_telegram_channel(raw: bytes, feed: dict) -> list[dict]:
    """One item per text message. The message is its own article: a channel
    post has no separate page, so run_once uses inline_article instead of
    fetching one."""
    parser = TelegramChannelParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    items = []
    for message in parser.messages[-30:]:
        text = re.sub(r"[ \t ]+", " ", message["text"]).strip()
        text = TELEGRAM_TIME_MARKER.sub("", text).strip()
        if not text:
            continue
        first = re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)[0].strip()
        title = first if len(first) <= 200 else first[:200].rsplit(" ", 1)[0]
        body = re.sub(r"\s+", " ", text).strip()
        items.append({
            "title": strip_html(title),
            "summary": body,
            "link": f"https://t.me/{message['post']}",
            "published": message["published"],
            "source": feed["name"],
            "category": feed.get("category", "general"),
            "priority": int(feed.get("priority", 3)),
            "region": feed.get("region", "global"),
            "sourceTier": feed.get("sourceTier", "secondary"),
            "articleHtmlAllowed": False,
            "inline_article": body,
            "minimumTextChars": int(feed.get("minimumTextChars", 120)),
        })
    return items
```

In `run_once`, replace the article step (the block starting `if config["posting"].get("requireSourceArticle", True):` through `item["source_article_verified"] = True` in its `else:` branch, lines 1423-1446) with:

```python
        if item.get("inline_article") is not None:
            # The source's own post is the article (a Telegram channel has no
            # separate page), so there is nothing to fetch.
            inline = re.sub(r"\s+", " ", strip_urls(item["inline_article"])).strip()
            if len(inline) < int(item.get("minimumTextChars", 120)):
                rejected.append(f"{item['source']}: article-text-too-thin :: {item['title'][:120]}")
                continue
            item["article_text"] = inline
            item["source_article_verified"] = True
        elif config["posting"].get("requireSourceArticle", True):
            if article_checks >= max_article_checks:
                rejected.append(f"{item['source']}: article-check-budget-exhausted :: {item['title'][:120]}")
                continue
            article_checks += 1
            article_config = config.get("articleContent", {})
            if not article_config.get("preferHtml", True) or item.get("articleHtmlAllowed") is False:
                rejected.append(f"{item['source']}: article-html-not-allowed :: {item['title'][:120]}")
                continue
            article_text, article_issue = source_article_text(
                item,
                timeout=int(article_config.get("timeoutSeconds", 12)),
                max_bytes=int(article_config.get("maxBytes", 2_000_000)),
                min_chars=int(article_config.get("minimumTextChars", 240)),
                max_chars=int(article_config.get("maximumTextChars", 12_000)),
            )
            if article_issue:
                rejected.append(f"{item['source']}: {article_issue} :: {item['title'][:120]}")
                continue
            item["article_text"] = article_text
            item["source_article_verified"] = True
        else:
            item["article_text"] = item.get("summary", "")
            item["source_article_verified"] = True
```

- [ ] **Step 4: Run tests**

Run: `python scripts/test_telegram_public.py` and `python scripts/test_tinnhanh247_reliability.py`
Expected: `All Telegram channel tests passed.`; reliability exit 0.

- [ ] **Step 5: Full suite, commit**

```bash
git add scripts/fastnews247_mvp.py scripts/test_telegram_public.py
git commit -m "Read public Telegram channels as a source; the post is the article

Coin369 posts Vietnamese flash news with no article page, so the message text
is used as the verified article and no fetch is attempted.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Conditional GET with a feed cache

**Files:**
- Modify: `scripts/fastnews247_mvp.py`: `fetch_url` (lines 122-142), `parse_feed` (from Task 3), `run_once` start and end
- Test: `scripts/test_feed_cache.py`

**Interfaces:**
- Produces:
  - `class FeedNotModified(Exception)`
  - `FEED_CACHE: dict` of url → `{"etag", "lastModified", "items"}`
  - `FEED_CACHE_PATH = Path("storage/fastnews247/feed_cache.json")`
  - `fetch_url(url, timeout=10, validators=None) -> bytes`, which raises `FeedNotModified` on 304
  - `FETCH_AUDIT[url]["etag"]`

- [ ] **Step 1: Write the failing test**

Create `scripts/test_feed_cache.py`:

```python
"""Offline tests for conditional GET on feeds.

A 2-minute timer over 50 feeds is only polite if unchanged feeds answer 304.
The contract: validators from the last 200 are sent back, a 304 returns the
cached items (never an empty list - an item skipped last run for budget
reasons must stay a candidate), and feeds without validators are not cached.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
FEED = {"name": "fixture", "url": "https://example.test/rss"}
RSS = (b"<rss><channel><item><title>Gold rises 2 percent on Fed bets</title>"
       b"<pubDate>Fri, 25 Sep 2026 03:00:00 GMT</pubDate><link>https://example.test/a</link></item>"
       b"</channel></rss>")


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict):
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def opener(*steps):
    def _open(request, timeout=None):
        _open.requests.append(request)
        step = steps[len(_open.requests) - 1]
        if isinstance(step, Exception):
            raise step
        return step
    _open.requests = []
    return _open


def not_modified():
    return urllib.error.HTTPError(FEED["url"], 304, "Not Modified", {}, io.BytesIO(b""))


def main() -> int:
    real = urllib.request.urlopen
    try:
        bot.FEED_CACHE.clear()
        print("first fetch stores validators and items")
        urllib.request.urlopen = opener(_Response(RSS, {"ETag": '"v1"', "Last-Modified": "Fri, 25 Sep 2026 03:00:00 GMT"}))
        items = bot.parse_feed(FEED)
        check("one item parsed", len(items) == 1, items)
        cached = bot.FEED_CACHE.get(FEED["url"], {})
        check("etag cached", cached.get("etag") == '"v1"', cached)
        check("items cached", len(cached.get("items", [])) == 1, cached)
        check("no validators on a cold fetch",
              urllib.request.urlopen.requests[0].get_header("If-none-match") is None)

        print("304 returns cached items as copies")
        urllib.request.urlopen = opener(not_modified())
        again = bot.parse_feed(FEED)
        sent = urllib.request.urlopen.requests[0]
        check("If-None-Match sent", sent.get_header("If-none-match") == '"v1"', sent.headers)
        check("If-Modified-Since sent", sent.get_header("If-modified-since") == "Fri, 25 Sep 2026 03:00:00 GMT",
              sent.headers)
        check("cached items returned", [i["title"] for i in again] == [items[0]["title"]], again)
        again[0]["title"] = "mutated by run_once"
        check("returned items are copies", bot.FEED_CACHE[FEED["url"]]["items"][0]["title"] != "mutated by run_once")

        print("no validators -> not cached")
        urllib.request.urlopen = opener(_Response(RSS, {}))
        bot.parse_feed(FEED)
        check("entry dropped", FEED["url"] not in bot.FEED_CACHE, bot.FEED_CACHE.keys())

        print("other HTTP errors still raise")
        urllib.request.urlopen = opener(urllib.error.HTTPError(FEED["url"], 500, "err", {}, io.BytesIO(b"")))
        try:
            bot.parse_feed(FEED)
            check("500 raises", False)
        except urllib.error.HTTPError as exc:
            check("500 raises", exc.code == 500)

        print("run_once loads and saves the cache, keeping only fresh items")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_file = root / bot.FEED_CACHE_PATH
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                FEED["url"]: {"etag": '"v9"', "items": [
                    {"title": "old", "published": "Mon, 01 Jan 2024 00:00:00 GMT"}]},
                "https://gone.test/rss": {"etag": '"x"', "items": []}}), encoding="utf-8")
            config = {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
                      "posting": {"telegram": {"mode": "direct"}, "duplicateWindowHours": 72,
                                  "minimumScoreToDraft": 4, "minimumScoreToPost": 4, "maxPostsPerRun": 1,
                                  "maximumAgeMinutes": 480},
                      "feeds": [FEED]}
            loaded = {}

            def fake_parse(feed):
                loaded.update(bot.FEED_CACHE)
                return []

            with patch.object(bot, "ROOT", root), patch.object(bot, "parse_feed", side_effect=fake_parse):
                bot.run_once(config, post=False)
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            check("cache loaded before fetching", loaded.get(FEED["url"], {}).get("etag") == '"v9"', loaded)
            check("feeds no longer configured are dropped", "https://gone.test/rss" not in saved, saved.keys())
            check("stale items dropped on save", saved[FEED["url"]]["items"] == [], saved[FEED["url"]])
    finally:
        urllib.request.urlopen = real
        bot.FEED_CACHE.clear()

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All feed-cache tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_feed_cache.py`
Expected: FAIL. `bot.FEED_CACHE` does not exist (AttributeError).

- [ ] **Step 3: Implement**

Replace `fetch_url` (lines 122-142) with:

```python
class FeedNotModified(Exception):
    """The server answered 304: the feed is unchanged since the cached copy."""


FEED_CACHE: dict = {}
FEED_CACHE_PATH = Path("storage/fastnews247/feed_cache.json")


def fetch_url(url: str, timeout: int = 10, validators: dict | None = None) -> bytes:
    headers = {
        "User-Agent": "TinNhanh247/1.0",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    if validators:
        if validators.get("etag"):
            headers["If-None-Match"] = validators["etag"]
        if validators.get("lastModified"):
            headers["If-Modified-Since"] = validators["lastModified"]
    request = urllib.request.Request(url, headers=headers)
    try:
        opened = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            raise FeedNotModified(url) from None
        raise
    with opened as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("response-too-large")
        FETCH_AUDIT[url] = {"httpDate": response.headers.get("Date"),
                            "age": response.headers.get("Age"),
                            "lastModified": response.headers.get("Last-Modified"),
                            "etag": response.headers.get("ETag"),
                            "cache": response.headers.get("X-Cache"),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        return raw
```

Replace `parse_feed` (from Task 3) with:

```python
def parse_feed(feed: dict) -> list[dict]:
    url = feed["url"]
    timeout = int(feed.get("timeoutSeconds", 10))
    cached = FEED_CACHE.get(url) or {}
    validators = {key: cached[key] for key in ("etag", "lastModified") if cached.get(key)}
    try:
        raw = (fetch_url(url, timeout=timeout, validators=validators) if validators
               else fetch_url(url, timeout=timeout))
    except FeedNotModified:
        # Unchanged feed: hand back the cached items, not nothing. An item a
        # previous run skipped for budget reasons must stay a candidate.
        return [dict(item) for item in cached.get("items", [])]
    if feed.get("type") == "telegram_public":
        items = parse_telegram_channel(raw, feed)
    else:
        items = parse_rss_items(raw, feed)
    audit = FETCH_AUDIT.get(url, {})
    if audit.get("etag") or audit.get("lastModified"):
        FEED_CACHE[url] = {"etag": audit.get("etag"), "lastModified": audit.get("lastModified"),
                           "items": [dict(item) for item in items]}
    else:
        FEED_CACHE.pop(url, None)
    return items
```

In `run_once`, immediately after `state = prune_state(...)` add:

```python
    cache_path = ROOT / FEED_CACHE_PATH
    FEED_CACHE.clear()
    FEED_CACHE.update(load_json(cache_path, {}) or {})
```

In `run_once`, immediately before the final `save_json(state_path, state)` (the one just before `print(f"Drafts: ...")`) add:

```python
    horizon = time.time() - int(config["posting"].get("maximumAgeMinutes", 240)) * 60
    configured = {feed["url"] for feed in config["feeds"]}
    save_json(cache_path, {
        url: {**entry, "items": [cached_item for cached_item in entry.get("items", [])
                                 if parse_time(cached_item.get("published", "")) >= horizon]}
        for url, entry in FEED_CACHE.items() if url in configured})
```

- [ ] **Step 4: Run tests**

Run: `python scripts/test_feed_cache.py`, `python scripts/test_telegram_public.py`, `python scripts/test_tinnhanh247_reliability.py`, `python scripts/test_feed_fallback.py`
Expected: all pass.

- [ ] **Step 5: Full suite, commit**

```bash
git add scripts/fastnews247_mvp.py scripts/test_feed_cache.py
git commit -m "Send ETag/Last-Modified back and reuse cached items on 304

Keeps a 2-minute cadence polite. A 304 returns the cached items rather than
nothing, so an item skipped for budget reasons stays a candidate.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Cost and duplicate guards in run_once

**Files:**
- Modify: `scripts/fastnews247_mvp.py`: `draft_post` (sets `vi_title`), `run_once` candidate loop and write-ahead record, new helper after `same_event`
- Test: `scripts/test_run_once_guards.py`

**Interfaces:**
- Consumes: `same_event`, `looks_vietnamese`
- Produces:
  - `duplicates_posted_vietnamese(title: str, state: dict, selected: list[dict]) -> bool`
  - `draft_post` sets `item["vi_title"]` when it returns a draft
  - state gains `"rejected": {fingerprint: epoch}`
  - seen records gain `"postedTitle"`
  - config key `posting.rejectRetryMinutes` (default 60)

- [ ] **Step 1: Write the failing test**

Create `scripts/test_run_once_guards.py`:

```python
"""Offline tests for the guards that keep a paid editor affordable.

1. An item the gates rejected is not re-drafted (re-paid) every 2 minutes.
2. The per-source cap is applied before drafting, not after paying for it.
3. Vietnamese titles are compared with what was already published, so an
   English source and a Vietnamese one about the same event post only once.
"""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as n  # noqa: E402

TOKEN_ENV = "TEST_GUARD_TOKEN"
POSTED_VI = "Vàng tăng 2,5% sau khi Fed giữ nguyên lãi suất trong cuộc họp tháng 9"


def config(**posting):
    base = {"telegram": {"mode": "direct", "tokenEnv": TOKEN_ENV, "channelId": "@example"},
            "duplicateWindowHours": 72, "minimumScoreToDraft": 4, "minimumScoreToPost": 4,
            "maxPostsPerRun": 5, "requireSourceArticle": False}
    base.update(posting)
    return {"channel": {"stateDb": "state.json", "draftOutput": "draft.md"},
            "posting": base, "feeds": [{"name": "fixture", "url": "https://example.test/feed"}]}


def news(title, source="fixture"):
    return {"title": title, "source": source, "summary": "",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "link": "https://example.test/" + str(abs(hash(title)))}


def seed_posted(root):
    state = {"schemaVersion": 2, "seen": {"old": {
        "time": time.time(), "title": "Gold climbs after Fed holds", "status": "confirmed",
        "postedTitle": POSTED_VI}}}
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


class Guards(unittest.TestCase):
    def setUp(self):
        os.environ[TOKEN_ENV] = "123:fake"

    def run_with(self, root, items, draft, send=None, **posting):
        send = send or (lambda *a: {"status": "confirmed", "reason": "telegram-message-ack",
                                    "messageId": 1, "chatId": "-100"})
        with patch.object(n, "ROOT", root), patch.object(n, "parse_feed", return_value=items), \
                patch.object(n, "score_item", return_value=(4, [], "fixture")), \
                patch.object(n, "draft_post", side_effect=draft) as drafted, \
                patch.object(n, "telegram_post", side_effect=send) as sent:
            n.run_once(config(**posting), post=True)
        return drafted, sent

    def test_gate_rejection_is_remembered_then_retried_after_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            items = [news("Fed holds rates at 5 percent after the September meeting")]
            reject = lambda item, *a: ("", ["headline-too-thin"])  # noqa: E731
            drafted, _ = self.run_with(root, items, reject)
            self.assertEqual(drafted.call_count, 1)
            drafted, _ = self.run_with(root, items, reject)
            self.assertEqual(drafted.call_count, 0, "rejected item was re-drafted within the window")
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            state["rejected"] = {key: 0 for key in state["rejected"]}
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            drafted, _ = self.run_with(root, items, reject)
            self.assertEqual(drafted.call_count, 1, "expired rejection was not retried")

    def test_per_source_cap_applies_before_drafting(self):
        with tempfile.TemporaryDirectory() as d:
            items = [news("Gold rises 2 percent as the dollar slips on Fed bets"),
                     news("Oil climbs 3 percent after OPEC agrees to extend cuts")]
            ok = lambda item, *a: (item.__setitem__("vi_title", item["title"]) or ("draft", []))  # noqa: E731
            drafted, sent = self.run_with(Path(d), items, ok, maxPostsPerSourcePerRun=1)
            self.assertEqual(drafted.call_count, 1)
            self.assertEqual(sent.call_count, 1)

    def test_english_item_duplicating_a_posted_vietnamese_title_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_posted(root)
            items = [news("Bullion gains 2.5 percent as Federal Reserve keeps rates unchanged", "other")]

            def rewrite(item, *a):
                item["vi_title"] = POSTED_VI
                return "draft", []

            drafted, sent = self.run_with(root, items, rewrite)
            self.assertEqual(drafted.call_count, 1)
            self.assertEqual(sent.call_count, 0)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["rejected"]), 1, "duplicate should be remembered, not re-paid")

    def test_vietnamese_source_duplicate_is_dropped_before_paying(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed_posted(root)
            drafted, sent = self.run_with(root, [news(POSTED_VI, "Coin369")], lambda item, *a: ("draft", []))
            self.assertEqual(drafted.call_count, 0)
            self.assertEqual(sent.call_count, 0)

    def test_posted_title_is_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            vi = "Dầu tăng 3% sau khi OPEC đồng ý gia hạn cắt giảm sản lượng"
            ok = lambda item, *a: (item.__setitem__("vi_title", vi) or ("draft", []))  # noqa: E731
            self.run_with(root, [news("Oil climbs 3 percent after OPEC agrees to extend output cuts")], ok)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual([v.get("postedTitle") for v in state["seen"].values()], [vi])

    def test_helper(self):
        state = {"seen": {"a": {"postedTitle": POSTED_VI}}}
        self.assertTrue(n.duplicates_posted_vietnamese(POSTED_VI, state, []))
        self.assertTrue(n.duplicates_posted_vietnamese(POSTED_VI, {"seen": {}}, [{"vi_title": POSTED_VI}]))
        self.assertFalse(n.duplicates_posted_vietnamese("", state, []))
        self.assertFalse(n.duplicates_posted_vietnamese(
            "Dầu tăng 3% sau khi OPEC đồng ý gia hạn cắt giảm sản lượng", state, []))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_run_once_guards.py`
Expected: failures/errors, including `AttributeError: ... 'duplicates_posted_vietnamese'`.

- [ ] **Step 3: Implement**

In `draft_post`, directly after:

```python
    if issues:
        return "", issues
```

add:

```python
    item["vi_title"] = title
```

After `same_event` add:

```python
def duplicates_posted_vietnamese(title: str, state: dict, selected: list[dict]) -> bool:
    """Compare a Vietnamese title with the Vietnamese titles already published.

    same_event compares words, so an English source and a Vietnamese one about
    the same event can only meet here, after both are in Vietnamese.
    """
    if not title:
        return False
    others = [record.get("postedTitle", "") for record in state.get("seen", {}).values()]
    others += [chosen.get("vi_title", "") for chosen in selected]
    return any(other and same_event({"title": title}, {"title": other}) for other in others)
```

In `run_once`, after the `seen_titles = {...}` set comprehension add:

```python
    # Items the gates rejected, remembered so a paid editor is not asked to
    # rewrite the same failing item every cycle until it goes stale.
    reject_window = int(config["posting"].get("rejectRetryMinutes", 60)) * 60
    rejected_at = {key: when for key, when in state.get("rejected", {}).items()
                   if when >= time.time() - reject_window}
```

In the feed loop change

```python
                if item["fingerprint"] in state["seen"] or normalized_title in seen_titles:
```

to

```python
                if (item["fingerprint"] in state["seen"] or normalized_title in seen_titles
                        or item["fingerprint"] in rejected_at):
```

In the candidate loop, directly after the `duplicate-event` block

```python
        if any(same_event(item, previous) for previous in list(state["seen"].values()) + selected):
            rejected.append(f"{item['source']}: duplicate-event :: {item['title']}")
            continue
```

insert:

```python
        # A source already in Vietnamese can be checked before paying for it.
        if looks_vietnamese(item.get("title", "")) and duplicates_posted_vietnamese(item["title"], state, selected):
            rejected.append(f"{item['source']}: duplicate-event-vi :: {item['title'][:120]}")
            continue
        # One source publishing a burst should not take every slot in a run.
        # Checked before drafting so no editor call is spent on an item that
        # would be dropped anyway. 0 disables the cap.
        per_source = int(config["posting"].get("maxPostsPerSourcePerRun", 0))
        if per_source > 0:
            taken = sum(1 for chosen in selected if chosen.get("source") == item.get("source"))
            if taken >= per_source:
                rejected.append(f"{item['source']}: source-quota-reached :: {item['title'][:120]}")
                continue
```

Replace the block after `draft_post`, from `if quality_issues:` through `selected.append(item)`, with:

```python
        if quality_issues:
            rejected_at[item["fingerprint"]] = time.time()
            rejected.append(f"{item['source']}: {','.join(quality_issues)} :: {item['title'][:120]}")
            continue
        if duplicates_posted_vietnamese(item.get("vi_title", ""), state, selected):
            rejected_at[item["fingerprint"]] = time.time()
            rejected.append(f"{item['source']}: duplicate-event-vi :: {item['title'][:120]}")
            continue
        item["draft"] = draft
        selected.append(item)
```

(The old per-source block that sat between `if quality_issues:` and `item["draft"] = draft` is removed; it now runs before drafting.)

In the write-ahead record, change

```python
                record = {"time": time.time(), "title": item["title"], "source": item["source"],
                          "score": item["score"], "link": item.get("link", ""),
                          "status": "pending", "reason": "write-ahead-send-intent"}
```

to

```python
                record = {"time": time.time(), "title": item["title"], "source": item["source"],
                          "score": item["score"], "link": item.get("link", ""),
                          "postedTitle": item.get("vi_title", ""),
                          "editorialPath": item.get("editorial_path", ""),
                          "status": "pending", "reason": "write-ahead-send-intent"}
```

Immediately before the final `save_json(state_path, state)`, and before the feed-cache save added in Task 4, add:

```python
    state["rejected"] = rejected_at
```

- [ ] **Step 4: Run tests**

Run: `python scripts/test_run_once_guards.py` then `python scripts/test_tinnhanh247_reliability.py`
Expected: `OK` for both.

- [ ] **Step 5: Full suite, commit**

```bash
git add scripts/fastnews247_mvp.py scripts/test_run_once_guards.py
git commit -m "Stop paying twice: remember rejections, cap per source before drafting

Rejected items are skipped for rejectRetryMinutes instead of being re-drafted
every cycle; the per-source cap now runs before the editor is called; posted
Vietnamese titles are kept so English and Vietnamese sources on the same
event publish once.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Telegram 429 handling

**Files:**
- Modify: `scripts/fastnews247_mvp.py`: `telegram_direct_post` (lines 1268-1308)
- Test: `scripts/test_telegram_direct.py` (extend)

**Interfaces:**
- Produces: `_telegram_retry_after(status: int, detail: str) -> float`. `telegram_direct_post` returns `reason: "telegram-rate-limited"` when it gives up on a 429.

- [ ] **Step 1: Write the failing test**

In `scripts/test_telegram_direct.py`, add after `stub_http_error`:

```python
def stub_sequence(*steps):
    def _open(request, timeout=None):
        _open.calls += 1
        step = steps[_open.calls - 1]
        if isinstance(step, Exception):
            raise step
        return _Response(json.dumps(step).encode("utf-8"))
    _open.calls = 0
    return _open


def too_many(retry_after: int):
    body = json.dumps({"ok": False, "error_code": 429,
                       "description": f"Too Many Requests: retry after {retry_after}",
                       "parameters": {"retry_after": retry_after}})
    return urllib.error.HTTPError("https://api.telegram.org/x", 429, "err", {}, io.BytesIO(body.encode("utf-8")))
```

In `main()`, after the "token redacted in detail" check and before `print("pre-flight checks")`, add:

```python
        print("429 handling")
        slept = []
        real_sleep = bot.time.sleep
        bot.time.sleep = slept.append
        try:
            urllib.request.urlopen = stub_sequence(too_many(3), {"ok": True, "result": {
                "message_id": 7, "chat": {"id": -100}}})
            out = bot.telegram_direct_post(TG, "x")
            check("short retry_after -> waits and succeeds", out.get("status") == "confirmed", out)
            check("waited retry_after", slept == [3.0], slept)

            slept.clear()
            urllib.request.urlopen = stub_sequence(too_many(120))
            out = bot.telegram_direct_post(TG, "x")
            check("long retry_after -> pending, no wait",
                  out.get("reason") == "telegram-rate-limited" and slept == [], (out, slept))

            slept.clear()
            urllib.request.urlopen = stub_sequence(too_many(2), too_many(2))
            out = bot.telegram_direct_post(TG, "x")
            check("retries only once", out.get("reason") == "telegram-rate-limited" and slept == [2.0],
                  (out, slept))
        finally:
            bot.time.sleep = real_sleep
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_telegram_direct.py`
Expected: `FAIL  short retry_after -> waits and succeeds`

- [ ] **Step 3: Implement**

Replace `telegram_direct_post` with:

```python
def _telegram_retry_after(status: int, detail: str) -> float:
    """Seconds Telegram asked us to wait, from a 429 body; 0 otherwise."""
    if status != 429:
        return 0.0
    try:
        return float((json.loads(detail).get("parameters") or {}).get("retry_after") or 0)
    except (ValueError, AttributeError, TypeError):
        return 0.0


def telegram_direct_post(tg: dict, text: str) -> dict:
    """Post straight to the Telegram Bot API.

    Reads the token from the environment name declared by tokenEnv and never
    writes it to logs. A 429 with a short retry_after is waited out once:
    Telegram did not accept the message, so resending cannot duplicate it.
    """
    token_env = tg.get("tokenEnv", "")
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"Missing Telegram bot token in ${token_env}.")
    channel_id = (os.environ.get(tg.get("channelEnv", ""), "") or tg.get("channelId", "")).strip()
    if not channel_id:
        raise RuntimeError("Missing Telegram channelId.")

    body = json.dumps({
        "chat_id": channel_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    for attempt in (0, 1):
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            wait = _telegram_retry_after(exc.code, detail)
            if attempt == 0 and 0 < wait <= 30:
                time.sleep(wait)
                continue
            reason = "telegram-rate-limited" if exc.code == 429 else "telegram-http-error"
            return {"status": "pending", "reason": reason,
                    "httpStatus": exc.code, "detail": _redact_token(detail, token)}
        except Exception as exc:
            return {"status": "pending", "reason": "telegram-request-failed",
                    "detail": _redact_token(str(exc), token)}

    ack = extract_ack(payload)
    if ack:
        return {"status": "confirmed", "reason": "telegram-message-ack", **ack}
    return {"status": "pending", "reason": "telegram-no-message-ack"}
```

- [ ] **Step 4: Run tests**

Run: `python scripts/test_telegram_direct.py`
Expected: `All direct-transport tests passed.`

- [ ] **Step 5: Full suite, commit**

```bash
git add scripts/fastnews247_mvp.py scripts/test_telegram_direct.py
git commit -m "Wait out a short Telegram 429 once instead of leaving the post pending

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Config, timer, healthcheck, docs

**Files:**
- Modify: `config/fastnews247.sources.json`, `deploy/fastnews247.timer`, `deploy/healthcheck.sh`, `README.md`, `.env.example`
- Test: `scripts/test_config_ladder.py`

**Interfaces:**
- Consumes: every config key read in Tasks 2–5.
- Produces: the shipped configuration, and `storage/fastnews247/subscription_quota.json` written by the healthcheck as `{"at": <epoch>, "usableProfiles": <int>}`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test_config_ladder.py`:

```python
"""Guards the shipped config: the values the spec fixed must stay fixed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    config = json.loads(bot.CONFIG_PATH.read_text(encoding="utf-8"))
    editorial = config["editorial"]
    api = editorial["openai"]
    sub = editorial["subscription"]
    posting = config["posting"]
    check("mode ladder", editorial["mode"] == "ladder", editorial["mode"])
    check("models", (api["model"], api["hotModel"], api["hotMinScore"]) == ("gpt-5.4-mini", "gpt-5.5", 5), api)
    check("budgets", (api["dailyBudgetUsd"], api["hotDailyBudgetUsd"]) == (3.0, 1.5), api)
    check("both models priced", all(model in api["pricesPerMTok"] for model in (api["model"], api["hotModel"])),
          api["pricesPerMTok"])
    check("key comes from env", api["apiKeyEnv"] == "OPENAI_API_KEY", api["apiKeyEnv"])
    check("subscription guard", (sub["maxCallsPerHour"], sub["minUsableProfiles"], sub["quotaCacheMaxAgeMinutes"])
          == (10, 2, 120), sub)
    check("direct transport", posting["telegram"]["mode"] == "direct", posting["telegram"])
    check("maxPostsPerRun 6", posting["maxPostsPerRun"] == 6, posting["maxPostsPerRun"])
    check("rejectRetryMinutes 60", posting.get("rejectRetryMinutes") == 60, posting.get("rejectRetryMinutes"))
    coin = [feed for feed in config["feeds"] if feed["name"] == "Coin369"]
    check("Coin369 feed present once", len(coin) == 1, coin)
    if coin:
        check("Coin369 shape", coin[0] == {"name": "Coin369", "type": "telegram_public",
                                           "url": "https://t.me/s/coin369channel", "category": "world_macro",
                                           "priority": 2, "sourceTier": "repost", "minimumTextChars": 80}, coin[0])
    names = [feed["name"] for feed in config["feeds"]]
    check("feed names unique", len(names) == len(set(names)), names)
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Config checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python scripts/test_config_ladder.py`
Expected: `FAIL  mode ladder auto`

- [ ] **Step 3: Edit the config**

In `config/fastnews247.sources.json`:

Replace the whole `"editorial": {...}` object with:

```json
  "editorial": {
    "mode": "ladder",
    "maxLlmCallsPerRun": 2,
    "openai": {
      "apiKeyEnv": "OPENAI_API_KEY",
      "model": "gpt-5.4-mini",
      "hotModel": "gpt-5.5",
      "hotMinScore": 5,
      "reasoningEffort": "low",
      "timeoutSeconds": 45,
      "maxOutputTokens": 2000,
      "sourceChars": 3000,
      "dailyBudgetUsd": 3.0,
      "hotDailyBudgetUsd": 1.5,
      "pricesPerMTok": {
        "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
        "gpt-5.5": {"input": 5.00, "output": 30.00}
      }
    },
    "subscription": {
      "enabled": true,
      "model": "openai/gpt-5.5",
      "maxCallsPerHour": 10,
      "minUsableProfiles": 2,
      "quotaCacheMaxAgeMinutes": 120,
      "timeoutSeconds": 180
    }
  },
```

In `"posting"`, set `"maxPostsPerRun": 6`, add `"rejectRetryMinutes": 60` after it, and in `"telegram"` set `"mode": "direct"`.

Append to the end of the `"feeds"` array:

```json
    {"name": "Coin369", "type": "telegram_public", "url": "https://t.me/s/coin369channel", "category": "world_macro", "priority": 2, "sourceTier": "repost", "minimumTextChars": 80}
```

Validate with `python -c "import json;json.load(open('config/fastnews247.sources.json',encoding='utf-8'))"` (expect no output).

- [ ] **Step 4: Timer**

Replace `deploy/fastnews247.timer` with:

```ini
[Unit]
Description=Run Tin nhanh 247 every 2 minutes

[Timer]
# Scanning is cheap (conditional GET, no LLM); the editor is only paid for
# items that pass scoring, dedupe and the per-source cap. worker.lock keeps a
# slow run from overlapping the next one.
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Healthcheck**

In `deploy/healthcheck.sh`:

Change `QUIET_HOURS_LIMIT=12      # no post for this long -> problem` to `QUIET_HOURS_LIMIT=3       # no post for this long -> problem`.

After the `if printf '%s' "$models" | grep -q "status=missing"; then ... fi` block, add:

```bash
# --- tai khoan subscription cho bac du phong ------------------------------
# openclaw khong doc duoc han muc tuan cua OpenAI ("Unsupported provider"),
# nen bot dung so tai khoan OAuth khong bi cooldown lam tin hieu thay the.
usable=$(printf '%s' "$models" | grep -oP 'openai:[^=,| ]+=OAuth \([^)]*\)(?! \[cooldown)' | wc -l)
note "tai khoan OAuth dung duoc" "$usable"
quota_file="$BOT_DIR/storage/fastnews247/subscription_quota.json"
mkdir -p "$(dirname "$quota_file")"
printf '{"at": %s, "usableProfiles": %s}\n' "$(date +%s)" "$usable" > "$quota_file.tmp" \
  && mv "$quota_file.tmp" "$quota_file"

# --- chi phi API va loi key -----------------------------------------------
ledger_report=$(python3 - "$BOT_DIR" <<'PY'
import datetime as dt, json, sys, time
root = sys.argv[1]
try:
    config = json.load(open(f"{root}/config/fastnews247.sources.json", encoding="utf-8"))
    ledger = json.load(open(f"{root}/storage/fastnews247/llm_ledger.json", encoding="utf-8"))
except (OSError, ValueError):
    print("note|chi phi API|chua co so ghi")
    sys.exit(0)
api = config.get("editorial", {}).get("openai", {})
today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()
day = ledger.get("days", {}).get(today, {})
spend, cap = float(day.get("spendUsd", 0)), float(api.get("dailyBudgetUsd", 0))
tiers = ", ".join(f"{k} {v}" for k, v in sorted(day.get("byTier", {}).items())) or "chua goi"
print(f"note|chi phi API hom nay|{spend:.2f}/{cap:.2f} USD ({tiers})")
if cap and spend >= cap:
    print("problem|Da cham tran chi phi API hom nay - dang dung du phong")
if float(ledger.get("apiDisabledUntil", 0) or 0) > time.time():
    print(f"problem|OpenAI API dang bi tat ({ledger.get('lastApiError', '?')}) - kiem tra key/so du")
if day.get("errors", {}).get("no-api-key"):
    print("problem|Chua co OPENAI_API_KEY trong .env - bot dang dung dich may")
PY
)
while IFS='|' read -r kind first second; do
  case "$kind" in
    note) note "$first" "$second" ;;
    problem) problems+=("$first") ;;
  esac
done <<< "$ledger_report"
```

Replace the alert block at the end (from `if [ "${1:-}" = "--alert" ]` to its `fi`) with:

```bash
send_alert() {
  local msg="$1" token
  token=$(sed -n 's/^FASTNEWS247_TELEGRAM_BOT_TOKEN=//p' "$BOT_DIR/.env" 2>/dev/null | head -1 | tr -d '\r"')
  if [ -n "$token" ]; then
    # The URL carries the token, so it goes to curl on stdin, not on argv.
    if printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
        | curl -s -m 20 --config - --data-urlencode "chat_id=$ALERT_TARGET" \
               --data-urlencode "text=$msg" | grep -q '"ok":true'; then
      return 0
    fi
  fi
  # Fallback through the gateway, for when the Bot API itself is the problem.
  openclaw message send --channel telegram --target "$ALERT_TARGET" --message "$msg" >/dev/null 2>&1
}

if [ "${1:-}" = "--alert" ] && [ -n "$ALERT_TARGET" ]; then
  msg="Tin nhanh 247 - canh bao $(date '+%d/%m %H:%M')"
  for p in "${problems[@]}"; do msg="$msg"$'\n'"- $p"; done
  send_alert "$msg" \
    && echo "      (da gui canh bao toi $ALERT_TARGET)" \
    || echo "      (GUI CANH BAO THAT BAI)"
fi
```

Check syntax and the profile regex:

```bash
bash -n deploy/healthcheck.sh && echo syntax-ok
printf '%s' 'openai:a@x.com=OAuth (a@x.com) [cooldown 5d], openai:b@x.com=OAuth (b@x.com), openai:c=OAuth (c)' \
  | grep -oP 'openai:[^=,| ]+=OAuth \([^)]*\)(?! \[cooldown)' | wc -l
```

Expected: `syntax-ok`, then `2`.

- [ ] **Step 6: Docs**

In `.env.example`, after the `FASTNEWS247_TELEGRAM_CHANNEL_ID` line add:

```bash

# editorial.mode "ladder": OpenAI API key for the paid editor tier. Without it
# the bot falls back to the subscription (if allowed) and machine translation.
OPENAI_API_KEY=
```

In `README.md`, replace the heading `## Ba chế độ biên tập` with `## Bốn chế độ biên tập` and add this row to its table:

```markdown
| `ladder` | OpenAI API viết trước (5 sao: `gpt-5.5`, còn lại: `gpt-5.4-mini`), subscription dự phòng có kiểm soát, dịch máy là sàn | Trả phí, có trần `dailyBudgetUsd` |
```

Then, directly after that table, add:

```markdown
### `ladder` — chế độ đang chạy trên VPS

- Chi phí thật ghi ở `storage/fastnews247/llm_ledger.json`, theo ngày giờ Việt
  Nam. Vượt `dailyBudgetUsd` thì ngừng gọi API; tin HOT có ngân sách riêng
  `hotDailyBudgetUsd`.
- Key hỏng hoặc hết tiền (401/403/`insufficient_quota`): API tự tắt 30 phút và
  `healthcheck.sh` báo về Telegram.
- Subscription chỉ được gọi khi API không dùng được, còn ≥ 2 tài khoản OAuth
  không bị cooldown (healthcheck ghi vào `subscription_quota.json` mỗi giờ), và
  chưa quá 10 lần trong giờ.
- Tin trượt cổng được ghi nhớ 60 phút (`rejectRetryMinutes`) để không phải trả
  tiền viết lại mỗi 2 phút.
```

- [ ] **Step 7: Run tests**

Run: `python scripts/test_config_ladder.py`, then the full-suite loop.
Expected: `Config checks passed.`; all `ok`.

- [ ] **Step 8: Commit**

```bash
git add config/fastnews247.sources.json deploy/fastnews247.timer deploy/healthcheck.sh README.md .env.example scripts/test_config_ladder.py
git commit -m "Ship the ladder: config, 2-minute timer, Bot API alerts, ledger checks

Posting moves to the direct Bot API so a slow gateway no longer blocks the
channel. The healthcheck alerts without the gateway, flags a dead or missing
OpenAI key and a reached cap, and records usable OAuth accounts for the
subscription guard.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Rollout on the VPS (operations; no new code)

Every command runs as `openclaw` via `ssh vps-oc`, with `export XDG_RUNTIME_DIR=/run/user/$(id -u)` first. Record each output in the session. Steps marked **USER** wait for the user.

- [ ] **Step 1: Push the branch** (laptop)

```bash
cd ~/AutoTinTuc && git push -u origin feat/single-publisher-ladder
```

- [ ] **Step 2: Back up and stop the competing publisher** (VPS)

```bash
~/bin/openclaw-backup.sh && tail -1 ~/backups/backup.log
openclaw cron disable 6292919d-20ec-4f7c-8dcf-52de69b7cf9f
openclaw cron disable f4d68eec-7d66-4952-889e-3ebce7db8074
openclaw cron list
```

Expected: `backup ok`; both jobs show disabled.

- [ ] **Step 3: Deploy the branch and run the tests on the VPS**

```bash
cd ~/AutoTinTuc && git fetch origin && git checkout feat/single-publisher-ladder && git log --oneline -1
for t in scripts/test_*.py; do python3 "$t" >/dev/null 2>&1 && echo "ok   $t" || echo "FAIL $t"; done
```

Expected: all `ok`. The timer keeps running the old cadence with the new code. That is safe: without a key, the ladder records `no-api-key` and uses translation.

- [ ] **Step 4: Dry run without posting**

```bash
cd ~/AutoTinTuc && set -a && . ./.env && set +a
python3 scripts/fastnews247_mvp.py --once
python3 - <<'PY'
import json
r = json.load(open("outputs/fastnews247/last_run.json"))
coin = [f for f in r["feeds"] if f["source"] == "Coin369"]
print("Coin369:", coin)
print("errors:", r["errors"])
print("selected:", [(s["source"], s.get("editorial_path"), s.get("vi_title")) for s in r["selected"]])
PY
```

Expected: Coin369 shows `items` > 0 and `fresh` > 0; `selected` lists drafts with `editorial_path` `translate` (no key yet). If the run prints `Skipped: another process owns the news lock.`, repeat it.

- [ ] **Step 5: Switch the timer to 2 minutes**

```bash
cp ~/AutoTinTuc/deploy/fastnews247.timer ~/.config/systemd/user/fastnews247.timer
systemctl --user daemon-reload && systemctl --user restart fastnews247.timer
systemctl --user list-timers fastnews247.timer --no-pager
```

Expected: next run in ≤ 2 minutes.

- [ ] **Step 6: Clean up OpenClaw**

```bash
openclaw sessions cleanup --dry-run --json | head -c 2000; echo
```

Show the dry-run summary to the user. Then:

```bash
openclaw sessions cleanup --enforce
openclaw sessions delete agent:main:fastnews247-editor --agent main --yes
openclaw models fallbacks remove openai/gpt-5.4-pro
openclaw models fallbacks remove custom-localhost-20128/openclaw
openclaw models fallbacks list
systemctl --user restart openclaw-gateway.service
```

Then poll until the gateway answers (boot can take minutes), and measure:

```bash
until curl -s -f -m 5 http://127.0.0.1:18789/healthz >/dev/null; do sleep 15; done
curl -s -o /dev/null -m 30 -w "%{time_total}s\n" http://127.0.0.1:18789/
systemctl --user show openclaw-gateway.service -p MemoryCurrent
openclaw status | grep -E "Gateway|Sessions"
```

Expected: HTTP well under the 13.6 s baseline, and memory well under 4.4 GB.

- [ ] **Step 7: USER: allow disabling the laptop publisher and gateway**

Ask the user. Only after a yes, on the laptop (PowerShell):

```powershell
Disable-ScheduledTask -TaskName "OpenClaw Fast News 247"
Disable-ScheduledTask -TaskName "OpenClaw Gateway"
Get-ScheduledTask | Where-Object { $_.TaskName -match 'OpenClaw' } | Select-Object TaskName, State
```

Expected: both `Disabled`.

- [ ] **Step 8: USER: add the OpenAI key**

The user creates a key at platform.openai.com, adds credit, sets a monthly budget limit (suggested 90 USD), and adds the line `OPENAI_API_KEY=...` to `~/AutoTinTuc/.env` on the VPS. The assistant never types the key. Afterwards, verify through the free models endpoint without printing the key:

```bash
cd ~/AutoTinTuc && set -a && . ./.env && set +a
python3 - <<'PY'
import json, os, urllib.request
req = urllib.request.Request("https://api.openai.com/v1/models",
                             headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
ids = {m["id"] for m in json.load(urllib.request.urlopen(req, timeout=20))["data"]}
print({m: m in ids for m in ("gpt-5.4-mini", "gpt-5.5")})
PY
```

Expected: `{'gpt-5.4-mini': True, 'gpt-5.5': True}`.

- [ ] **Step 9: A/B sample for the user (throwaway, not committed)**

Write `/tmp/ab_compare.py` on the VPS:

```python
import concurrent.futures, os, sys
sys.path.insert(0, "scripts")
import fastnews247_llm as llm
import fastnews247_mvp as bot

config = bot.load_json(bot.CONFIG_PATH, None)
api = config["editorial"]["openai"]
key = os.environ[api["apiKeyEnv"]]


def safe(feed):
    try:
        return bot.parse_feed(feed)
    except Exception:
        return []


with concurrent.futures.ThreadPoolExecutor(8) as pool:
    everything = [item for batch in pool.map(safe, config["feeds"]) for item in batch]
fresh = []
for item in everything:
    if bot.freshness_issue(item, config):
        continue
    score, tags, _ = bot.score_item(item, config)
    if score >= 4:
        item.update(score=score, tags=tags)
        fresh.append(item)
fresh.sort(key=lambda i: (i["score"], bot.parse_time(i["published"])), reverse=True)
picked = []
for item in fresh:
    if any(bot.same_event(item, other) for other in picked):
        continue
    if item.get("inline_article"):
        item["article_text"] = item["inline_article"]
    else:
        text, issue = bot.source_article_text(item, timeout=8)
        if issue:
            continue
        item["article_text"] = text
    item["source_article_verified"] = True
    picked.append(item)
    if len(picked) == 20:
        break

total = 0.0
for item in picked:
    prompt, source_title, source_body = bot._editorial_prompt(item, int(api.get("sourceChars", 3000)))
    print("=" * 78)
    print(f"[{item['score']}*] {item['source']}: {item['title']}")
    for model in (api["model"], api["hotModel"]):
        spent = []
        try:
            reply = llm.openai_editorial(prompt, model, api, key,
                                         lambda u: spent.append(llm.cost_usd(u, model, api["pricesPerMTok"])))
            title, summary = bot._validated_rewrite(reply, source_title, source_body)
            issues = (bot.headline_quality_issues(title, item) + bot.summary_quality_issues(summary, item, title)
                      if title else ["fact-gate"])
            print(f"  --- {model}  ${sum(spent):.4f}  {'DAT' if not issues else issues}")
            print(f"  🔹 {reply.get('title')}")
            print(f"  📝 {reply.get('summary')}")
        except llm.ApiError as err:
            print(f"  --- {model}: LOI {err.kind}")
        total += sum(spent)
print(f"\nTong chi phi A/B: ${total:.3f}")
```

Run it with the key loaded, and send the output file to the user:

```bash
cd ~/AutoTinTuc && set -a && . ./.env && set +a && python3 /tmp/ab_compare.py > /tmp/ab_compare.txt; tail -1 /tmp/ab_compare.txt
```

Copy it to the laptop with `scp vps-oc:/tmp/ab_compare.txt <scratchpad>/` and deliver it with SendUserFile. Expected cost: about 0.5–1 USD. **USER** confirms the model choice before continuing. A different choice is a one-line config change.

- [ ] **Step 10: Watch two hours of live running**

At about 30, 60 and 120 minutes, run:

```bash
journalctl --user -u fastnews247.service --since "-30min" --no-pager -o cat | grep -cE "Posting live"
journalctl --user -u fastnews247.service --since "-30min" --no-pager -o cat | grep -E "Delivery:" | grep -vc '"confirmed"'
python3 -c "import json;d=json.load(open('storage/fastnews247/llm_ledger.json'));import pprint;pprint.pprint(d['days'])"
bash deploy/healthcheck.sh
```

Expected:
- posts > 0 with 0 unconfirmed;
- ledger `byTier` shows mostly `openai`/`openai-hot`, and spend is tracking toward about 2 USD/day;
- healthcheck prints no ledger problems. It may still show gateway, OAuth-token or cooldown lines, which concern the agent, not the channel.

Also read the channel and confirm there are no duplicate events.

- [ ] **Step 11: Merge**

When Step 10 is clean, use superpowers:finishing-a-development-branch to merge `feat/single-publisher-ladder` into `main`, push, and switch the VPS checkout to `main`:

```bash
cd ~/AutoTinTuc && git fetch origin && git checkout main && git pull --ff-only && git log --oneline -1
```

- [ ] **Step 12: Rollback (only if needed)**

```bash
cd ~/AutoTinTuc && git checkout 4e9f76e
sed -i 's/OnUnitActiveSec=2min/OnUnitActiveSec=10min/' ~/.config/systemd/user/fastnews247.timer
systemctl --user daemon-reload && systemctl --user restart fastnews247.timer
```

Commit `4e9f76e` has `bridge` + `auto` in its own config, so checking it out restores the old behaviour. The OpenClaw cron stays disabled either way.
