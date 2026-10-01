"""Launch a disposable mock server, exercise HTTP, then close only our own process."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "CASE_SIM_TEST_PORT": str(port), "PYTHONUTF8": "1"}
    # Logs belong to this disposable child, never read the user's runtime logs or .env.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([sys.executable, "-m", "backend.tests.student_ui_server"],
                                   cwd=ROOT, env=env, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(timeout=1) as client:
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("isolated server exited before readiness")
                    try:
                        if client.get(f"http://127.0.0.1:{port}/api/health").json() == {"status": "ok"}:
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("isolated server readiness timed out")
                    time.sleep(0.1)
            subprocess.run([sys.executable, "-m", "backend.tests.architecture_http_smoke"],
                           cwd=ROOT, env=env, check=True, timeout=60)
        except Exception:
            output.seek(0)
            print(output.read().decode("utf-8", errors="replace")[-12000:])
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            print(f"Isolated mock server closed (pid={process.pid}, port={port}).")


if __name__ == "__main__":
    main()
