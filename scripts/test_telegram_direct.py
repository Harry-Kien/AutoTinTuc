"""Offline tests for the 'direct' Telegram transport.

No network: urlopen is stubbed. Verifies the transport keeps the same contract
as the bridge (confirmed only on a real message ack) and never leaks the token.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

TOKEN = "123456789:AAFAKEfaketokenfaketokenfaketoken1234"
TG = {"mode": "direct", "tokenEnv": "TEST_TG_TOKEN",
      "channelEnv": "TEST_TG_CHANNEL", "channelId": "@example"}

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
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


def stub_urlopen(payload: dict):
    def _open(request, timeout=None):
        _open.last_request = request
        return _Response(json.dumps(payload).encode("utf-8"))
    _open.last_request = None
    return _open


def stub_http_error(code: int, body: str):
    def _open(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, code, "err", {}, io.BytesIO(body.encode("utf-8")))
    return _open


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


def main() -> int:
    os.environ["TEST_TG_TOKEN"] = TOKEN
    os.environ.pop("TEST_TG_CHANNEL", None)
    real = urllib.request.urlopen

    print("direct transport")
    try:
        # A genuine Telegram ack must be reported as confirmed.
        opener = stub_urlopen({"ok": True, "result": {
            "message_id": 4242, "chat": {"id": -1001234567890}}})
        urllib.request.urlopen = opener
        out = bot.telegram_direct_post(TG, "xin chao")
        check("ack -> confirmed", out.get("status") == "confirmed", out)
        check("messageId kept", out.get("messageId") == 4242, out)
        check("posts to sendMessage",
              opener.last_request.full_url.endswith("/sendMessage"),
              opener.last_request.full_url.replace(TOKEN, "<token>"))
        sent = json.loads(opener.last_request.data.decode("utf-8"))
        check("body carries chat_id + text",
              sent.get("chat_id") == "@example" and sent.get("text") == "xin chao", sent)

        # Telegram answering without a message id is NOT a delivery guarantee.
        urllib.request.urlopen = stub_urlopen({"ok": True, "result": {}})
        out = bot.telegram_direct_post(TG, "x")
        check("no ack -> pending", out.get("status") == "pending", out)

        # Errors must degrade to pending and must not echo the token.
        urllib.request.urlopen = stub_http_error(401, f"unauthorized {TOKEN}")
        out = bot.telegram_direct_post(TG, "x")
        check("http error -> pending", out.get("status") == "pending", out)
        check("token redacted in detail", TOKEN not in json.dumps(out), "TOKEN LEAKED")

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

        print("pre-flight checks")
        cfg = {"posting": {"telegram": dict(TG, enabled=True)}}
        try:
            bot.assert_posting_ready(cfg)
            check("valid config passes", True)
        except Exception as exc:  # noqa: BLE001
            check("valid config passes", False, exc)

        os.environ["TEST_TG_TOKEN"] = ""
        try:
            bot.assert_posting_ready(cfg)
            check("missing token rejected", False, "no error raised")
        except RuntimeError as exc:
            check("missing token rejected", "TEST_TG_TOKEN" in str(exc), exc)
            check("error names env, not value", TOKEN not in str(exc), "TOKEN LEAKED")

        os.environ["TEST_TG_TOKEN"] = TOKEN
        cfg_bad = {"posting": {"telegram": dict(TG, mode="carrier-pigeon", enabled=True)}}
        try:
            bot.assert_posting_ready(cfg_bad)
            check("unknown mode rejected", False, "no error raised")
        except RuntimeError:
            check("unknown mode rejected", True)

        print("bridge is untouched")
        cfg_bridge = {"posting": {"telegram": {
            "enabled": True, "mode": "bridge", "channelId": "@example"}}}
        try:
            bot.assert_posting_ready(cfg_bridge)
            check("bridge config still valid", True)
        except Exception as exc:  # noqa: BLE001
            check("bridge config still valid", False, exc)
    finally:
        urllib.request.urlopen = real

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All direct-transport tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
