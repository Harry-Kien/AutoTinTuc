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
    check("subscription first, API right behind", editorial.get("order") == ["subscription", "openai"],
          editorial.get("order"))
    check("pause after a silent subscription", editorial["subscription"].get("pauseMinutesAfterFailure") == 15,
          editorial["subscription"])
    check("rollback path intact", (editorial.get("model"), editorial.get("sessionKey"))
          == ("openai/gpt-5.5", "agent:main:fastnews247-editor"),
          (editorial.get("model"), editorial.get("sessionKey")))
    check("models", (api["model"], api["hotModel"], api["hotMinScore"]) == ("gpt-5.4-mini", "gpt-5.5", 5), api)
    check("budgets", (api["dailyBudgetUsd"], api["hotDailyBudgetUsd"]) == (3.0, 1.5), api)
    check("API circuit breaker pause 5 min", api.get("pauseMinutesAfterFailure") == 5, api.get("pauseMinutesAfterFailure"))
    check("both models priced", all(model in api["pricesPerMTok"] for model in (api["model"], api["hotModel"])),
          api["pricesPerMTok"])
    check("key comes from env", api["apiKeyEnv"] == "OPENAI_API_KEY", api["apiKeyEnv"])
    check("subscription guard", (sub["maxCallsPerHour"], sub["minUsableProfiles"], sub["quotaCacheMaxAgeMinutes"])
          == (10, 1, 120), sub)
    check("direct transport", posting["telegram"]["mode"] == "direct", posting["telegram"])
    check("maxPostsPerRun 6", posting["maxPostsPerRun"] == 6, posting["maxPostsPerRun"])
    check("rejectRetryMinutes 60", posting.get("rejectRetryMinutes") == 60, posting.get("rejectRetryMinutes"))
    coin = [feed for feed in config["feeds"] if feed["name"] == "Coin369"]
    check("Coin369 feed present once", len(coin) == 1, coin)
    if coin:
        check("Coin369 shape", coin[0] == {"name": "Coin369", "type": "telegram_public",
                                           "url": "https://t.me/s/coin369channel", "category": "world_macro",
                                           "priority": 4, "sourceTier": "repost", "minimumTextChars": 80}, coin[0])
    check("vietnamMarket no longer keyed on the generic word for bank",
          not any(term in ("ngân hàng", "ngan hang") for term in config["assets"]["vietnamMarket"]),
          config["assets"]["vietnamMarket"])
    check("macroPolicy bucket present", "macroPolicy" in config["assets"], list(config["assets"]))
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
