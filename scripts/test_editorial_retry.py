"""Offline tests for the LLM retry when quality gates reject a translation.

The rule being protected: the retry may produce better prose, but it is held
to the identical gates, it costs budget, and it never fires when the gates
already passed or when the first attempt was itself the LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
ITEM = {"title": "Gold climbs after Fed holds", "source": "Test", "category": "world_macro",
        "source_sentences": ["Gold climbed after the Fed held rates steady, analysts said."],
        "article_text": "Gold climbed after the Fed held rates steady, analysts said."}
CFG = {"posting": {"minimumScoreToPost": 4}, "editorial": {"mode": "auto"}}


def check(name: str, cond: bool, detail: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


def main() -> int:
    saved = (bot.editorial_for, bot.openclaw_rewrite,
             bot.headline_quality_issues, bot.summary_quality_issues)
    try:
        calls = {"n": 0}

        def llm(item, cfg):
            calls["n"] += 1
            return ("Vàng tăng sau khi Fed giữ lãi suất",
                    "Vàng tăng sau khi Fed giữ nguyên lãi suất, theo giới phân tích.")

        print("dich may trươt cong -> LLM viet lai, qua cong")
        calls["n"] = 0
        bot.openclaw_rewrite = llm
        bot.editorial_for = lambda item, c=None, b=None: (
            item.__setitem__("editorial_path", "translate") or ("Tieu de kem", "Tom tat kem"))
        # cong chi chap nhan ban cua LLM
        bot.headline_quality_issues = lambda t, i: [] if t.startswith("Vàng") else ["main-event-not-explicit"]
        bot.summary_quality_issues = lambda s, i, t: [] if s.startswith("Vàng") else ["summary-lacks-subject-action-context"]
        budget = {"remaining": 2}
        draft, issues = bot.draft_post(dict(ITEM), 5, ["#VANG"], CFG, budget)
        check("cuu duoc tin", draft != "" and not issues, issues)
        check("goi LLM dung 1 lan", calls["n"] == 1, calls["n"])
        check("tru dung 1 ngan sach", budget["remaining"] == 1, budget)

        print("dich may qua cong -> KHONG goi LLM, khong ton ngan sach")
        calls["n"] = 0
        bot.headline_quality_issues = lambda t, i: []
        bot.summary_quality_issues = lambda s, i, t: []
        budget = {"remaining": 2}
        draft, issues = bot.draft_post(dict(ITEM), 5, ["#VANG"], CFG, budget)
        check("ra ban nhap", draft != "", issues)
        check("khong goi LLM", calls["n"] == 0, calls["n"])
        check("ngan sach nguyen ven", budget["remaining"] == 2, budget)

        print("het ngan sach -> khong goi LLM, tin bi loai")
        calls["n"] = 0
        bot.headline_quality_issues = lambda t, i: ["main-event-not-explicit"]
        bot.summary_quality_issues = lambda s, i, t: []
        budget = {"remaining": 0}
        draft, issues = bot.draft_post(dict(ITEM), 5, ["#VANG"], CFG, budget)
        check("bi loai khi het ngan sach", draft == "" and issues, (draft, issues))
        check("khong goi LLM", calls["n"] == 0, calls["n"])

        print("mode translate -> khong bao gio goi LLM")
        calls["n"] = 0
        budget = {"remaining": 5}
        draft, issues = bot.draft_post(dict(ITEM), 5, ["#VANG"],
                                       {"posting": {}, "editorial": {"mode": "translate"}}, budget)
        check("mode translate khong goi LLM", calls["n"] == 0, calls["n"])
        check("ngan sach nguyen ven", budget["remaining"] == 5, budget)

        print("ban LLM cung trươt cong -> VAN bi loai (khong ha chuan)")
        calls["n"] = 0
        bot.headline_quality_issues = lambda t, i: ["main-event-not-explicit"]
        bot.summary_quality_issues = lambda s, i, t: ["summary-lacks-subject-action-context"]
        budget = {"remaining": 2}
        draft, issues = bot.draft_post(dict(ITEM), 5, ["#VANG"], CFG, budget)
        check("LLM kem cung bi loai", draft == "" and issues, (draft, issues))
        check("van tru ngan sach da dung", budget["remaining"] == 1, budget)

        print("lan dau da la LLM -> khong thu lai lan nua")
        calls["n"] = 0
        bot.editorial_for = lambda item, c=None, b=None: (
            item.__setitem__("editorial_path", "openclaw") or ("Tieu de kem", "Tom tat kem"))
        budget = {"remaining": 3}
        draft, issues = bot.draft_post(dict(ITEM), 5, ["#VANG"], CFG, budget)
        check("khong goi LLM lan hai", calls["n"] == 0, calls["n"])
        check("ngan sach nguyen ven", budget["remaining"] == 3, budget)
    finally:
        (bot.editorial_for, bot.openclaw_rewrite,
         bot.headline_quality_issues, bot.summary_quality_issues) = saved

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All editorial-retry tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
