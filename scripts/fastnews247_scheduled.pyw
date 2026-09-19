"""Windowless Task Scheduler entry; preserve exit status and durable logs."""
import datetime
import os
from pathlib import Path
import runpy
import sys
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
(ROOT / "logs").mkdir(exist_ok=True)
with open(ROOT / "logs/fastnews247_scheduler.log", "a", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log
    print(f"[{datetime.datetime.now().isoformat()}] Tin nhanh 247 cycle start pid={os.getpid()}", flush=True)
    sys.argv = [str(ROOT / "scripts/fastnews247_mvp.py"), "--once", "--post"]
    code = 1
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
        code = 0
    except SystemExit as exc:
        code = exc.code or 0
    except BaseException:
        import traceback
        traceback.print_exc()
    print(f"[{datetime.datetime.now().isoformat()}] Tin nhanh 247 cycle end exit={code}", flush=True)
    raise SystemExit(code)
