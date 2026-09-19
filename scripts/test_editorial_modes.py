"""Offline tests for editorial routing and the OpenClaw rewrite gates.

No network, no subprocess: resolve_openclaw_cli and subprocess.run are stubbed.
The contract under test is that the LLM may write better prose but never gets
looser fact checks than the machine-translation path, and never costs more
quota than editorial.maxLlmCallsPerRun.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastnews247_mvp as bot  # noqa: E402

failures: list[str] = []
ITEM = {
    "title": "Gold climbs 2.5% as the Fed holds rates",
    "source_sentences": ["Gold climbed 2.5% on Tuesday after the Fed held rates steady, analysts said."],
}


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


class Recorder:
    """Stands in for subprocess.run and remembers how it was called."""

    def __init__(self, reply: object):
        self.reply = reply
        self.calls: list[list[str]] = []
        self.prompts: list[str] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        for index, part in enumerate(command):
            if part == "--message-file":
                self.prompts.append(Path(command[index + 1]).read_text(encoding="utf-8"))
        body = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return types.SimpleNamespace(stdout=json.dumps({"text": body}), stderr="", returncode=0)


def install(reply: object, translate_ok: bool) -> Recorder:
    recorder = Recorder(reply)
    bot.subprocess.run = recorder
    bot.resolve_openclaw_cli = lambda: ["node", "/fake/openclaw.mjs"]
    good = ("Vàng tăng 2,5% sau khi Fed giữ nguyên lãi suất",
            "Vàng tăng 2,5% hôm thứ Ba sau khi Fed giữ nguyên lãi suất, theo các nhà phân tích.")
    bot.vietnamese_editorial = (lambda item: good) if translate_ok else (lambda item: ("", ""))
    return recorder


def main() -> int:
    saved = (bot.subprocess.run, bot.resolve_openclaw_cli, bot.vietnamese_editorial)
    llm = {"title": "Vàng tăng 2,5% khi Fed giữ nguyên lãi suất",
           "summary": "Vàng tăng 2,5% hôm thứ Ba sau khi Fed giữ nguyên lãi suất, theo giới phân tích."}
    try:
        print("mode translate (mac dinh) khong bao gio goi LLM")
        rec = install(llm, translate_ok=False)
        title, _ = bot.editorial_for(ITEM, {}, {"remaining": 5})
        check("translate that bai -> bo tin", title == "", title)
        check("khong goi OpenClaw", len(rec.calls) == 0, rec.calls)

        print("mode auto chi goi LLM khi translate that bai")
        rec = install(llm, translate_ok=True)
        cfg = {"editorial": {"mode": "auto"}}
        title, _ = bot.editorial_for(ITEM, cfg, {"remaining": 5})
        check("translate thanh cong -> khong ton quota", len(rec.calls) == 0, rec.calls)
        check("dung ket qua translate", title.startswith("Vàng tăng"), title)

        rec = install(llm, translate_ok=False)
        budget = {"remaining": 5}
        title, summary = bot.editorial_for(ITEM, cfg, budget)
        check("translate hong -> LLM cuu tin", title == llm["title"], title)
        check("tieu ton dung 1 luot", budget["remaining"] == 4, budget)
        check("goi qua --message-file", "--message-file" in rec.calls[0], rec.calls[0])
        check("noi dung bai KHONG nam tren dong lenh",
              not any("Fed held rates" in part for part in rec.calls[0]), rec.calls[0])
        check("prompt chua tieu de goc", "Gold climbs" in rec.prompts[0], rec.prompts[0][:80])

        print("ngan sach quota duoc ton trong")
        rec = install(llm, translate_ok=False)
        budget = {"remaining": 1}
        bot.editorial_for(ITEM, cfg, budget)
        bot.editorial_for(ITEM, cfg, budget)
        bot.editorial_for(ITEM, cfg, budget)
        check("het ngan sach thi dung goi", len(rec.calls) == 1, f"{len(rec.calls)} luot goi")
        check("ngan sach khong am", budget["remaining"] == 0, budget)

        print("mode openclaw uu tien LLM, translate la du phong")
        rec = install(llm, translate_ok=True)
        title, _ = bot.editorial_for(ITEM, {"editorial": {"mode": "openclaw"}}, {"remaining": 2})
        check("dung ket qua LLM", title == llm["title"], title)
        rec = install("khong phai JSON", translate_ok=True)
        title, _ = bot.editorial_for(ITEM, {"editorial": {"mode": "openclaw"}}, {"remaining": 2})
        check("LLM hong -> quay ve translate", title.startswith("Vàng tăng"), title)

        print("LLM khong duoc noi long kiem tra du kien")
        install({"title": "Vàng tăng 7,9% khi Fed giữ nguyên lãi suất",
                 "summary": "Vàng tăng 7,9% hôm thứ Ba, theo giới phân tích."}, translate_ok=False)
        title, _ = bot.editorial_for(ITEM, cfg, {"remaining": 3})
        check("bia so lieu -> bi loai", title == "", title)

        install({"title": "Gold rises after Fed decision",
                 "summary": "Gold rose 2.5% on Tuesday after the Fed decision."}, translate_ok=False)
        title, _ = bot.editorial_for(ITEM, cfg, {"remaining": 3})
        check("khong phai tieng Viet -> bi loai", title == "", title)

        install({"title": "", "summary": ""}, translate_ok=False)
        title, _ = bot.editorial_for(ITEM, cfg, {"remaining": 3})
        check("tra ve rong -> bi loai", title == "", title)

        print("CLI khong co thi that bai kin, khong lam sap luot chay")
        bot.resolve_openclaw_cli = lambda: (_ for _ in ()).throw(RuntimeError("not found"))
        title, _ = bot.editorial_for(ITEM, cfg, {"remaining": 3})
        check("thieu OpenClaw -> tra rong, khong nem loi", title == "", title)
    finally:
        bot.subprocess.run, bot.resolve_openclaw_cli, bot.vietnamese_editorial = saved

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All editorial routing tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
