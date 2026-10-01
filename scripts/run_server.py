"""Start the server plus the explicitly requested visible log console."""
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env")
    path = ROOT / "logs" / (datetime.now().strftime("runtime-%Y%m%d-%H%M%S") + f"-{os.getpid()}.log")
    os.environ["CASE_SIM_LOG_FILE"] = str(path)
    from backend.app.observability import configure_logging
    from backend.app.observability import event
    from backend.app.observability import log
    configure_logging()
    if os.name == "nt" and "--no-log-window" not in sys.argv:
        try:
            viewer = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "scripts" / "watch_logs.py"), str(path)],
                cwd=str(ROOT), creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            event("独立实时日志窗口进程已启动", pid=viewer.pid)
        except OSError:
            log.exception("独立日志窗口打开失败；仍可查看原窗口及日志文件")
    import uvicorn
    try:
        event("服务器启动中", host=os.getenv("APP_HOST", "0.0.0.0"),
              port=os.getenv("APP_PORT", "8100"))
        uvicorn.run("backend.app.main:app", host=os.getenv("APP_HOST", "0.0.0.0"),
                    port=int(os.getenv("APP_PORT", "8100")), log_config=None, access_log=False)
    except BaseException:
        log.exception("服务器退出或启动失败")
        raise
    finally:
        event("服务器已停止", note="日志文件保留；日志窗口可单独关闭")


if __name__ == "__main__":
    main()
