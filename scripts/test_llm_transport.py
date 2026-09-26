"""Offline tests for the paid-API and subscription transports and the ledger.

No network, no subprocess: urllib.request.urlopen and subprocess.run are
stubbed. The contract: every billed attempt is counted, keys never leak,
dead keys switch the API off instead of being retried, and the subscription
is only touched when its guard allows it.
"""
from __future__ import annotations

import io
import json
import os
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

            print("subscription_block_reason survives a malformed quota value")
            check("list quota -> quota-unknown", llm.subscription_block_reason(sub, empty, [], NOW) == "quota-unknown")
            check("string at -> quota-unknown",
                  llm.subscription_block_reason(sub, empty, {"at": "soon", "usableProfiles": 3}, NOW)
                  == "quota-unknown")

            print("subscription pause")
            paused = llm.Ledger(Path(directory) / "paused.json", load, save)
            check("not paused by default", not paused.subscription_paused(NOW))
            paused.pause_subscription("no-reply", NOW, 900)
            check("paused inside the window", paused.subscription_paused(NOW + 899))
            check("resumes after the window", not paused.subscription_paused(NOW + 900))
            check("pause reason kept", paused.data["lastSubscriptionError"] == "no-reply")
            check("block reason paused", llm.subscription_block_reason(sub, paused, fresh, NOW + 60) == "paused")
            check("disabled still wins over paused",
                  llm.subscription_block_reason(dict(sub, enabled=False), paused, fresh, NOW) == "disabled")
            paused.save()
            check("pause persisted",
                  llm.Ledger(Path(directory) / "paused.json", load, save).subscription_paused(NOW + 60))

            print("api pause (circuit breaker for outages)")
            breaker = llm.Ledger(Path(directory) / "breaker.json", load, save)
            check("api not paused by default", not breaker.api_paused(NOW) and breaker.api_available(NOW))
            breaker.pause_api("network", NOW, 300)
            check("paused api is unavailable", not breaker.api_available(NOW + 299))
            check("api back after the pause", breaker.api_available(NOW + 300))
            check("pause reason kept", breaker.data["lastApiPauseReason"] == "network")
            check("a pause is not a disable", breaker.data["apiDisabledUntil"] == 0 and breaker.data["lastApiError"] == "")
            breaker.save()
            check("api pause persisted",
                  llm.Ledger(Path(directory) / "breaker.json", load, save).api_paused(NOW + 60))

            print("Ledger tolerates a wrong-shaped file")
            odd = llm.Ledger(Path(directory) / "odd.json",
                             lambda p, d: {"apiDisabledUntil": "soon", "days": [], "subscriptionCalls": None}, save)
            check("wrong types replaced", odd.data["days"] == {} and odd.data["apiDisabledUntil"] == 0.0, odd.data)
            check("repair flagged", odd.repaired is True)
            check("missing fields are not a repair", llm.Ledger(Path(directory) / "new.json", load, save).repaired is False)
            check("non-dict file treated as empty", llm.Ledger(Path(directory) / "l.json", lambda p, d: [1], save).data["days"] == {})

        print("scrubbed_env")
        os.environ["OPENAI_API_KEY"] = "sk-should-be-scrubbed"
        os.environ["TEST_TOKEN_ENV_NAME"] = "tok-should-be-scrubbed"
        try:
            scrubbed = llm.scrubbed_env("TEST_TOKEN_ENV_NAME")
            check("OPENAI_-prefixed vars dropped", "OPENAI_API_KEY" not in scrubbed, list(scrubbed))
            check("named var dropped", "TEST_TOKEN_ENV_NAME" not in scrubbed, list(scrubbed))
            check("name drop is case-insensitive",
                  "test_token_env_name" not in llm.scrubbed_env("test_token_env_name"), "n/a")
            check("PATH kept", "PATH" in scrubbed, list(scrubbed))
            check("HOME kept", "HOME" in scrubbed, list(scrubbed))
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["TEST_TOKEN_ENV_NAME"]

        print("subscription_editorial")
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = list(command)
            seen["env"] = kwargs.get("env")
            index = command.index("--message-file")
            seen["prompt"] = Path(command[index + 1]).read_text(encoding="utf-8")
            return types.SimpleNamespace(stdout='{"text": "ok"}', stderr="", returncode=0)

        llm.subprocess.run = fake_run
        os.environ["OPENAI_API_KEY"] = "sk-should-not-leak"
        os.environ["TEST_LLM_TOKEN"] = "tok-should-not-leak"
        try:
            out = llm.subscription_editorial("ARTICLE BODY", {"model": "openai/gpt-5.5", "timeoutSeconds": 60},
                                             ["node", "/fake/openclaw.mjs"])
            command = seen["command"]
            check("returns stdout", out == '{"text": "ok"}', out)
            check("isolated agent exec", command[2:4] == ["agent", "exec"], command)
            check("no persistent session", "--session-key" not in command, command)
            check("model passed", command[command.index("--model") + 1] == "openai/gpt-5.5", command)
            check("prompt via file, not argv", seen["prompt"] == "ARTICLE BODY"
                  and not any("ARTICLE BODY" in part for part in command), command)
            check("env passed by default", seen["env"] is not None, seen["env"])
            check("default env scrubs OPENAI_API_KEY", "OPENAI_API_KEY" not in seen["env"], list(seen["env"] or {}))
            check("default env keeps PATH", "PATH" in (seen["env"] or {}), list(seen["env"] or {}))
            check("default env keeps HOME", "HOME" in (seen["env"] or {}), list(seen["env"] or {}))

            explicit_env = {"CUSTOM": "1"}
            llm.subscription_editorial("ARTICLE BODY", {"model": "openai/gpt-5.5"},
                                       ["node", "/fake/openclaw.mjs"], env=explicit_env)
            check("explicit env passed through unchanged", seen["env"] == explicit_env, seen["env"])
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["TEST_LLM_TOKEN"]

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
