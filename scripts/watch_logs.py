"""Read-only UTF-8 log viewer. Reopens files to follow Windows log rotation."""
import codecs
import os
from pathlib import Path
import sys
import time


def chunks(path):
    offset, identity = 0, None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        try:
            with path.open("rb") as stream:
                stat = path.stat()
                current = (stat.st_dev, stat.st_ino)
                if identity != current or stat.st_size < offset:
                    offset = 0
                    decoder.reset()
                identity = current
                stream.seek(offset)
                data = stream.read(65536)
                offset = stream.tell()
            if data:
                yield decoder.decode(data)
                continue
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.3)


if __name__ == "__main__":
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("Case Sim - 教师端 + 学生端实时日志")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = Path(sys.argv[1]).resolve()
    print("Case Sim - 实时运行日志（教师端 + 学生端）", flush=True)
    print(f"日志文件：{path}\n关闭本窗口不会停止服务器。Ctrl+C 退出日志查看。", flush=True)
    try:
        for chunk in chunks(path):
            print(chunk, end="", flush=True)
    except KeyboardInterrupt:
        pass
