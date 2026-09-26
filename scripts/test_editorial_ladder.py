"""Offline tests for editorial.mode "ladder".

The transports are stubbed on the llm module; the ledger is real and lives in
a temporary ROOT. The contract: with the shipped order the free subscription
writes first and any failure hands the item to the paid API at once; a dead
subscription is paused so later items do not wait on it; hot items get the
strong model until their own budget runs out; the daily cap stops paid calls;
dead keys stop being called; a paid draft that invents facts falls to
translation instead of buying a second opinion.
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
TG_TOKEN_ENV = "TEST_LADDER_TG_TOKEN"
# The headline gate requires >=45 chars and >=7 words, so GOOD's title carries
# extra (fact-clean) context beyond the bare event; the summary must still add
# >=4 new terms over the title to clear the "adds no new information" gate.
GOOD = {"title": "Vàng tăng 2,5% khi Fed giữ nguyên lãi suất trong phiên thứ Ba",
        "summary": "Vàng tăng 2,5% hôm thứ Ba sau khi Fed giữ nguyên lãi suất, theo giới phân tích."}
INVENTED = {"title": "Vàng tăng 7,9% khi Fed giữ nguyên lãi suất",
            "summary": "Vàng tăng 7,9% hôm thứ Ba, theo giới phân tích."}
# Fact-clean (same numbers/claims as GOOD) but the old, too-thin 42-char title:
# passes _validated_rewrite, must still be rejected at tier acceptance.
THIN = {"title": "Vàng tăng 2,5% khi Fed giữ nguyên lãi suất",
        "summary": "Vàng tăng 2,5% hôm thứ Ba sau khi Fed giữ nguyên lãi suất, theo giới phân tích."}
TRANSLATED = ("Vàng tăng 2,5% sau quyết định của Fed", "Bản dịch máy của tin vàng hôm thứ Ba.")
# A translation stand-in that itself clears the headline/summary gates, for the
# end-to-end draft_post test where every ladder tier returns a thin draft.
TRANSLATED_OK = ("Vàng tăng 2,5% khi Fed giữ nguyên lãi suất theo bản dịch máy",
                 "Bản dịch máy cho biết vàng tăng 2,5% hôm thứ Ba nhờ lực mua từ nhà đầu tư.")


def make_config(order, max_per_hour=10):
    return {"editorial": {
        "mode": "ladder",
        "order": order,
        "openai": {"apiKeyEnv": KEY_ENV, "model": "gpt-5.4-mini", "hotModel": "gpt-5.5", "hotMinScore": 5,
                   "dailyBudgetUsd": 3.0, "hotDailyBudgetUsd": 1.5, "sourceChars": 3000,
                   "pricesPerMTok": {"gpt-5.4-mini": {"input": 0.75, "output": 4.50},
                                     "gpt-5.5": {"input": 5.00, "output": 30.00}}},
        "subscription": {"enabled": True, "model": "openai/gpt-5.5", "maxCallsPerHour": max_per_hour,
                         "minUsableProfiles": 2, "quotaCacheMaxAgeMinutes": 120,
                         "pauseMinutesAfterFailure": 15}},
        "posting": {"telegram": {"tokenEnv": TG_TOKEN_ENV}}}


SUB_FIRST = make_config(["subscription", "openai"])
API_FIRST = make_config(["openai", "subscription"])


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def item(score: int) -> dict:
    return {"title": "Gold climbs 2.5% as the Fed holds rates", "score": score,
            "source": "Test Wire",
            "source_article_verified": True,
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
    """reply None -> empty stdout (the tier gave nothing usable)."""

    def __init__(self, reply):
        self.reply, self.calls, self.envs = reply, 0, []

    def __call__(self, prompt, cfg, cli, env=None):
        self.calls += 1
        self.envs.append(env)
        return "" if self.reply is None else json.dumps({"text": json.dumps(self.reply)})


def ledger_data(root: Path) -> dict:
    return json.loads((root / bot.LEDGER_PATH).read_text(encoding="utf-8"))


def ledger_day(root: Path) -> dict:
    return ledger_data(root)["days"][llm.vietnam_day(time.time())]


def fresh_root(directory: str, quota: bool) -> Path:
    root = Path(directory)
    bot.ROOT = root
    if quota:
        (root / bot.QUOTA_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / bot.QUOTA_PATH).write_text(json.dumps({"at": time.time(), "usableProfiles": 3}),
                                           encoding="utf-8")
    return root


def install(api: FakeApi, sub: FakeSubscription) -> None:
    llm.openai_editorial = api
    llm.subscription_editorial = sub


def main() -> int:
    saved = (llm.openai_editorial, llm.subscription_editorial, bot.vietnamese_editorial,
             bot.resolve_openclaw_cli, bot.ROOT)
    os.environ[KEY_ENV] = "sk-test-ladder"
    os.environ[TG_TOKEN_ENV] = "tg-token-should-not-leak"
    bot.vietnamese_editorial = lambda it: TRANSLATED
    bot.resolve_openclaw_cli = lambda: ["node", "/fake/openclaw.mjs"]
    try:
        print("subscription first: healthy subscription writes, API untouched")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            news = item(5)
            title, summary = bot.editorial_for(news, SUB_FIRST, {"remaining": 0})
            check("subscription text used", (title, summary) == (GOOD["title"], GOOD["summary"]), title)
            check("path subscription", news.get("editorial_path") == "subscription", news.get("editorial_path"))
            check("API not called", api.calls == [], api.calls)
            check("free call costs nothing", ledger_day(root)["spendUsd"] == 0.0, ledger_day(root))
            check("counted as subscription", ledger_day(root)["byTier"] == {"subscription": 1}, ledger_day(root))
            check("subscription subprocess env scrubs the OpenAI key",
                  KEY_ENV not in (sub.envs[0] or {}), sub.envs[0])
            check("subscription subprocess env scrubs the Telegram token",
                  TG_TOKEN_ENV not in (sub.envs[0] or {}), sub.envs[0])
            check("subscription subprocess env keeps PATH", "PATH" in (sub.envs[0] or {}), sub.envs[0])

        print("subscription gives nothing -> paused, API at once, later items skip it")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(GOOD), FakeSubscription(None)
            install(api, sub)
            news = item(5)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("API rescued the item", title == GOOD["title"] and news["editorial_path"] == "openai-hot",
                  (title, news.get("editorial_path")))
            check("hot item -> gpt-5.5", api.calls[0]["model"] == "gpt-5.5", api.calls)
            data = ledger_data(root)
            check("subscription paused ~15 min", data["subscriptionPausedUntil"] > time.time() + 800,
                  data["subscriptionPausedUntil"])
            news = item(4)
            bot.editorial_for(news, SUB_FIRST, None)
            check("paused subscription not called again", sub.calls == 1, sub.calls)
            check("4-star -> gpt-5.4-mini", api.calls[1]["model"] == "gpt-5.4-mini", api.calls)
            check("path openai", news.get("editorial_path") == "openai", news.get("editorial_path"))
            day = ledger_day(root)
            check("spend 0.025 + 0.00375", abs(day["spendUsd"] - 0.02875) < 1e-9, day)
            check("hot spend only the hot call", abs(day["hotSpendUsd"] - 0.025) < 1e-9, day)
            check("prompt carries article text", "next inflation report" in api.calls[0]["prompt"],
                  api.calls[0]["prompt"][-200:])
            check("key never in prompt", "sk-test-ladder" not in api.calls[0]["prompt"])

        print("subscription invents a number -> API rewrites, no pause")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(GOOD), FakeSubscription(INVENTED)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("API draft used", title == GOOD["title"] and news["editorial_path"] == "openai", title)
            check("gate miss recorded", ledger_day(root)["errors"].get("subscription-gate-rejected") == 1,
                  ledger_day(root))
            check("gate miss does not pause", ledger_data(root)["subscriptionPausedUntil"] == 0)

        print("subscription draft is fact-clean but headline-too-thin -> API rewrites, no pause")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(GOOD), FakeSubscription(THIN)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("API's GOOD used after a thin subscription draft",
                  title == GOOD["title"] and news["editorial_path"] == "openai",
                  (title, news.get("editorial_path")))
            check("thin draft recorded as subscription-gate-rejected",
                  ledger_day(root)["errors"].get("subscription-gate-rejected") == 1, ledger_day(root))
            check("a thin (fact-clean) draft does not pause the subscription",
                  ledger_data(root)["subscriptionPausedUntil"] == 0)

        print("paid draft is fact-clean but headline-too-thin -> translation, no second opinion")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(THIN), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, API_FIRST, None)
            check("thin paid draft -> translation", title == TRANSLATED[0] and news["editorial_path"] == "translate",
                  title)
            check("no second opinion bought after a thin paid draft", sub.calls == 0, sub.calls)
            check("thin paid draft recorded as gate-rejected",
                  ledger_day(root)["errors"].get("gate-rejected") == 1, ledger_day(root))

        print("end to end: every ladder tier returns a thin draft -> draft_post still posts, from translation")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(THIN), FakeSubscription(THIN)
            install(api, sub)
            bot.vietnamese_editorial = lambda it: TRANSLATED_OK
            try:
                news = item(4)
                post, issues = bot.draft_post(news, 4, ["#XAUUSD"], dict(SUB_FIRST, posting={}), None)
                check("draft_post posts the translation when every ladder tier is thin",
                      bool(post) and not issues and news.get("editorial_path") == "translate",
                      (post, issues, news.get("editorial_path")))
            finally:
                bot.vietnamese_editorial = lambda it: TRANSLATED

        print("no quota file / hourly cap -> straight to API")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            bot.editorial_for(item(4), SUB_FIRST, None)
            check("no quota file -> subscription skipped", sub.calls == 0 and len(api.calls) == 1,
                  (sub.calls, len(api.calls)))
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            capped = make_config(["subscription", "openai"], max_per_hour=1)
            bot.editorial_for(item(4), capped, None)
            news = item(4)
            bot.editorial_for(news, capped, None)
            check("hourly cap -> second item goes to API", sub.calls == 1 and len(api.calls) == 1
                  and news["editorial_path"] == "openai", (sub.calls, len(api.calls)))

        print("an unreadable or malformed quota file never aborts the run -> subscription just gets skipped")
        malformed_quotas = ["", "{not json", json.dumps([1, 2, 3]),
                            json.dumps({"at": "soon", "usableProfiles": 3})]
        for payload in malformed_quotas:
            with tempfile.TemporaryDirectory() as directory:
                root = fresh_root(directory, quota=False)
                (root / bot.QUOTA_PATH).parent.mkdir(parents=True, exist_ok=True)
                (root / bot.QUOTA_PATH).write_text(payload, encoding="utf-8")
                api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
                install(api, sub)
                news = item(4)
                title, _ = bot.editorial_for(news, SUB_FIRST, None)
                check(f"malformed quota {payload[:20]!r} -> subscription skipped, API used",
                      sub.calls == 0 and title == GOOD["title"] and news["editorial_path"] == "openai",
                      (payload[:20], sub.calls, title, news.get("editorial_path")))

        print("a corrupt llm_ledger.json never aborts the run")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            (root / bot.LEDGER_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / bot.LEDGER_PATH).write_text("{not json", encoding="utf-8")
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("corrupt ledger -> item still posted via subscription", title == GOOD["title"], title)
            check("corrupt ledger recorded as ledger-unreadable",
                  ledger_day(root)["errors"].get("ledger-unreadable") == 1, ledger_day(root))

        print("a tier raising an unexpected exception falls to translation instead of crashing the run")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            api, sub = FakeApi(error=KeyError("output")), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("tier exception -> translation used",
                  title == TRANSLATED[0] and news["editorial_path"] == "translate", title)
            check("no quota file skips the subscription; the exploding tier is the API",
                  sub.calls == 0, sub.calls)
            check("tier exception recorded as ladder-exception-KeyError",
                  ledger_day(root)["errors"].get("ladder-exception-KeyError") == 1, ledger_day(root))

        print("hot budget spent -> hot item drops to mini; daily cap -> translation")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            bot.editorial_for(item(5), SUB_FIRST, None)
            data = ledger_data(root)
            data["days"][llm.vietnam_day(time.time())]["hotSpendUsd"] = 1.5
            (root / bot.LEDGER_PATH).write_text(json.dumps(data), encoding="utf-8")
            bot.editorial_for(item(5), SUB_FIRST, None)
            check("mini once hot budget is gone", api.calls[-1]["model"] == "gpt-5.4-mini", api.calls)
            data = ledger_data(root)
            data["days"][llm.vietnam_day(time.time())]["spendUsd"] = 3.0
            (root / bot.LEDGER_PATH).write_text(json.dumps(data), encoding="utf-8")
            calls_before = len(api.calls)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("API not called over cap", len(api.calls) == calls_before, api.calls)
            check("translation used", title == TRANSLATED[0] and news["editorial_path"] == "translate", title)

        print("paid draft that invents facts -> translation, subscription NOT tried after it")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(INVENTED), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, API_FIRST, None)
            check("fact gate rejects paid draft", title == TRANSLATED[0] and news["editorial_path"] == "translate",
                  title)
            check("no second opinion bought", sub.calls == 0, sub.calls)
            check("gate-rejected recorded", ledger_day(root)["errors"].get("gate-rejected") == 1, ledger_day(root))

        print("API first: transient error -> subscription")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api = FakeApi(error=llm.ApiError("rate_limit_exceeded", retryable=True))
            sub = FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            bot.editorial_for(news, API_FIRST, None)
            check("fell to subscription", news["editorial_path"] == "subscription" and sub.calls == 1,
                  (news.get("editorial_path"), sub.calls))
            check("transient error does not disable API", ledger_data(root)["apiDisabledUntil"] == 0)

        print("transient API error -> API paused: the next item makes no paid call (circuit breaker)")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            api, sub = FakeApi(error=llm.ApiError("network", retryable=True)), FakeSubscription(GOOD)
            install(api, sub)
            first = item(4)
            title, _ = bot.editorial_for(first, SUB_FIRST, None)
            check("first item -> translation after the failed call",
                  title == TRANSLATED[0] and len(api.calls) == 1, (title, len(api.calls)))
            second = item(4)
            title, _ = bot.editorial_for(second, SUB_FIRST, None)
            check("second item makes no API call while paused",
                  len(api.calls) == 1 and second["editorial_path"] == "translate",
                  (len(api.calls), second.get("editorial_path")))
            data = ledger_data(root)
            check("apiPausedUntil ~5 min ahead", data["apiPausedUntil"] > time.time() + 200, data.get("apiPausedUntil"))
            check("pause reason kept", data["lastApiPauseReason"] == "network", data.get("lastApiPauseReason"))
            check("a pause is not a disable (no dead-key alert)", data["apiDisabledUntil"] == 0)
            check("network error recorded once", ledger_day(root)["errors"].get("network") == 1, ledger_day(root))

        print("ledger cannot be written -> the cap fails closed: no paid call")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            (root / bot.LEDGER_PATH).mkdir(parents=True)  # a directory where the file must go
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("unwritable ledger -> no API call", api.calls == [], api.calls)
            check("unwritable ledger -> translation", title == TRANSLATED[0] and news["editorial_path"] == "translate",
                  (title, news.get("editorial_path")))

        print("a ledger file that is valid JSON but the wrong shape never aborts the run")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            (root / bot.LEDGER_PATH).parent.mkdir(parents=True, exist_ok=True)
            (root / bot.LEDGER_PATH).write_text(json.dumps({"apiDisabledUntil": "soon", "days": []}), encoding="utf-8")
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            title, _ = bot.editorial_for(news, SUB_FIRST, None)
            check("wrong-shape ledger -> item still written by the API", title == GOOD["title"], title)
            check("wrong-shape ledger recorded as ledger-unreadable",
                  ledger_day(root)["errors"].get("ledger-unreadable") == 1, ledger_day(root))

        print("missing key / dead key")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=False)
            os.environ[KEY_ENV] = ""
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            news = item(4)
            bot.editorial_for(news, SUB_FIRST, None)
            check("no call without key", api.calls == [] and news["editorial_path"] == "translate", api.calls)
            check("no-api-key recorded", ledger_day(root)["errors"].get("no-api-key") == 1, ledger_day(root))
            os.environ[KEY_ENV] = "sk-test-ladder"
            api = FakeApi(error=llm.ApiError("invalid_api_key", fatal=True))
            install(api, sub)
            bot.editorial_for(item(4), SUB_FIRST, None)
            bot.editorial_for(item(4), SUB_FIRST, None)
            check("dead key called only once", len(api.calls) == 1, len(api.calls))
            check("lastApiError kept", ledger_data(root)["lastApiError"] == "invalid_api_key")

        print("default order is API first; other modes untouched")
        with tempfile.TemporaryDirectory() as directory:
            root = fresh_root(directory, quota=True)
            api, sub = FakeApi(GOOD), FakeSubscription(GOOD)
            install(api, sub)
            no_order = make_config(["openai", "subscription"])
            del no_order["editorial"]["order"]
            bot.editorial_for(item(4), no_order, None)
            check("no order key -> API first", len(api.calls) == 1 and sub.calls == 0, (len(api.calls), sub.calls))
        check("translate mode ignores ladder config",
              bot.editorial_for(item(4), {"editorial": {"mode": "translate"}}, None)[0] == TRANSLATED[0])
    finally:
        (llm.openai_editorial, llm.subscription_editorial, bot.vietnamese_editorial,
         bot.resolve_openclaw_cli, bot.ROOT) = saved
        os.environ.pop(KEY_ENV, None)
        os.environ.pop(TG_TOKEN_ENV, None)

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All ladder tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
