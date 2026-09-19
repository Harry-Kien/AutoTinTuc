"""Offline tests for cross-platform OpenClaw CLI resolution.

No network, no subprocess: the filesystem and PATH lookups are redirected at
temporary directories. The point is that the working Windows invocation is
preserved byte for byte while Linux gains a way to find the CLI.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
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


def make_cli(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// stub", encoding="utf-8")
    return path


def main() -> int:
    saved_env = {k: os.environ.get(k) for k in ("OPENCLAW_CLI", "APPDATA", "HOME", "PATH")}
    saved_which = shutil.which
    saved_home = Path.home

    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Isolate: no launcher on PATH, no home-dir install, no real APPDATA.
            shutil.which = lambda *_a, **_k: None
            bot.shutil.which = shutil.which
            Path.home = classmethod(lambda cls: root / "nohome")  # type: ignore[assignment]
            os.environ.pop("OPENCLAW_CLI", None)
            os.environ["APPDATA"] = str(root / "noappdata")

            print("explicit override")
            os.environ["OPENCLAW_CLI"] = "/opt/openclaw/openclaw.mjs"
            check("mjs override runs under node",
                  bot.resolve_openclaw_cli() == ["node", "/opt/openclaw/openclaw.mjs"],
                  bot.resolve_openclaw_cli())
            os.environ["OPENCLAW_CLI"] = "/usr/bin/openclaw"
            check("binary override runs directly",
                  bot.resolve_openclaw_cli() == ["/usr/bin/openclaw"],
                  bot.resolve_openclaw_cli())
            os.environ.pop("OPENCLAW_CLI")

            print("windows layout is unchanged")
            appdata = root / "appdata"
            cli = make_cli(appdata, "npm/node_modules/openclaw/openclaw.mjs")
            os.environ["APPDATA"] = str(appdata)
            check("uses node + openclaw.mjs from APPDATA",
                  bot.resolve_openclaw_cli() == ["node", str(cli)],
                  bot.resolve_openclaw_cli())
            os.environ["APPDATA"] = str(root / "noappdata")

            print("linux layout")
            home = root / "home"
            Path.home = classmethod(lambda cls: home)  # type: ignore[assignment]
            linux_cli = make_cli(home, ".npm-global/lib/node_modules/openclaw/openclaw.mjs")
            check("finds npm-global install",
                  bot.resolve_openclaw_cli() == ["node", str(linux_cli)],
                  bot.resolve_openclaw_cli())
            linux_cli.unlink()

            print("launcher on PATH is the last resort, not the first choice")
            appdata2 = root / "appdata2"
            preferred = make_cli(appdata2, "npm/node_modules/openclaw/openclaw.mjs")
            os.environ["APPDATA"] = str(appdata2)
            bot.shutil.which = lambda *_a, **_k: r"C:\npm\openclaw.CMD"
            resolved = bot.resolve_openclaw_cli()
            check("module path beats the .CMD shim",
                  resolved == ["node", str(preferred)], resolved)
            check("headline text never reaches cmd.exe",
                  not any(str(part).lower().endswith(".cmd") for part in resolved), resolved)
            os.environ["APPDATA"] = str(root / "noappdata")
            preferred.unlink()
            check("falls back to the shim when nothing else exists",
                  bot.resolve_openclaw_cli() == [r"C:\npm\openclaw.CMD"],
                  bot.resolve_openclaw_cli())

            print("missing install fails loudly and says how to fix it")
            bot.shutil.which = lambda *_a, **_k: None
            os.environ["PATH"] = str(root / "emptybin")
            try:
                resolved = bot.resolve_openclaw_cli()
                check("raises when absent", False, f"returned {resolved}")
            except RuntimeError as exc:
                check("raises when absent", True)
                check("names the install command", "npm install -g openclaw" in str(exc), exc)
                check("names the override", "OPENCLAW_CLI" in str(exc), exc)
    finally:
        shutil.which = saved_which
        bot.shutil.which = saved_which
        Path.home = saved_home  # type: ignore[assignment]
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All OpenClaw bridge resolution tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
