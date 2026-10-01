"""Run five isolated test workers, a keyed gateway and an optional HTTPS tunnel.

Run from the project venv. Ctrl+C closes only children created by this runner.
No seed data and no copying of the developer database.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.public_gateway import create_gateway, read_spaces
from scripts.public_links import write_link_state


def provision(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Exclusive create: never rotate keys or reset data during a normal start.
        with path.open("x", encoding="utf-8") as file:
            json.dump({"spaces": [{"id": f"test-{i}", "key": secrets.token_urlsafe(32)}
                                  for i in range(1, 6)]}, file, ensure_ascii=False, indent=2)
    return read_spaces(path)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main():
    from scripts.process_group import own_child_processes
    job_handle = own_child_processes()
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true", help="Offline gateway acceptance; no tunnel")
    parser.add_argument("--tunnel-token-file", type=Path, default=None,
                        help="Use a persistent Cloudflare named tunnel token from this file")
    parser.add_argument("--public-hostname", default=None,
                        help="Stable HTTPS hostname configured for the named tunnel")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--root", type=Path, default=ROOT / "private" / "public-test")
    parser.add_argument("--offline-fixture", action="store_true", help="Acceptance only; mock AI, disposable test root required")
    args = parser.parse_args()
    token_file = args.tunnel_token_file
    if token_file is None and os.getenv("CLOUDFLARE_TUNNEL_TOKEN_FILE"):
        token_file = Path(os.environ["CLOUDFLARE_TUNNEL_TOKEN_FILE"])
    public_hostname = args.public_hostname or os.getenv("PUBLIC_HOSTNAME")
    named_tunnel = token_file is not None or public_hostname is not None
    if named_tunnel:
        if token_file is None or not token_file.is_file() or not token_file.read_text(encoding="utf-8").strip():
            raise RuntimeError("Named tunnel mode needs a non-empty token file (--tunnel-token-file or CLOUDFLARE_TUNNEL_TOKEN_FILE).")
        if not public_hostname or not re.fullmatch(r"[A-Za-z0-9.-]+", public_hostname) or "." not in public_hostname:
            raise RuntimeError("Named tunnel mode needs a valid stable hostname (--public-hostname or PUBLIC_HOSTNAME).")
        public_hostname = public_hostname.lower()
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env")
    folder = args.root.resolve()
    if args.offline_fixture and not folder.is_relative_to(ROOT / ".test-tmp"):
        raise RuntimeError("Offline fixtures must stay under .test-tmp, never real test spaces")
    config = folder / "access-keys.json"
    # Invalidate old URLs before doing any startup work so a failed restart
    # cannot leave yesterday's dead Quick Tunnel looking current.
    write_link_state(folder, status="starting")
    spaces = provision(config)
    if not spaces:
        raise RuntimeError("No active spaces. Add a key to access-keys.json before starting.")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("Build frontend first: cd frontend && npm run build")
    # Fail before opening a tunnel when the gateway port is already in use.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    processes, logs = [], []
    log_offsets = {}
    workers = {}
    try:
        for ident in spaces:
            space = folder / "spaces" / ident
            static = space / "static"
            static.mkdir(parents=True, exist_ok=True)
            runtime = space / "runtime.log"
            log_offsets[ident] = runtime.stat().st_size if runtime.exists() else 0
            port = free_port()
            password = secrets.token_urlsafe(32)
            env = dict(os.environ, DATABASE_URL="sqlite:///" + (space / "app.db").as_posix(),
                ARTIFACT_ROOT=str(space / "artifacts"), CASE_SIM_STATIC_DIR=str(static),
                CASE_SIM_LOG_FILE=str(space / "runtime.log"), TEACHER_PASSWORD=password,
                PUBLIC_BASE_URL="", PYTHONUTF8="1")
            out = (space / "process.log").open("a", encoding="utf-8")
            logs.append(out)
            entry = "backend.tests.public_mock_worker:app" if args.offline_fixture else "backend.app.main:app"
            if args.offline_fixture:
                env.update(LLM_BASE_URL="", LLM_API_KEY="", LLM_MODEL="", DEEPSEEK_API_KEY="", TAVILY_API_KEY="")
            child = subprocess.Popen([sys.executable, "-u", "-m", "uvicorn", entry,
                "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--proxy-headers",
                "--forwarded-allow-ips", "127.0.0.1"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
            processes.append(child)
            workers[ident] = {"url": f"http://127.0.0.1:{port}", "password": password}
        import httpx
        with httpx.Client(timeout=1, trust_env=False) as client:
            deadline = time.monotonic() + 40
            pending = set(workers)
            while pending and time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("A worker exited. Check private/public-test/spaces/*/process.log")
                for ident in list(pending):
                    try:
                        if client.get(workers[ident]["url"] + "/api/health").status_code == 200:
                            pending.remove(ident)
                    except httpx.HTTPError:
                        pass
                if pending:
                    time.sleep(.15)
            if pending:
                raise RuntimeError("Workers failed readiness: " + ", ".join(pending))
        origin = f"http://127.0.0.1:{args.port}"
        if not args.local_only:
            executable = ROOT / "tools" / "cloudflared.exe"
            if not executable.is_file():
                raise RuntimeError("Missing tools/cloudflared.exe; see docs/公网测试使用说明.md")
            tunnel_log = (folder / "tunnel.log").open("w", encoding="utf-8")
            logs.append(tunnel_log)
            tunnel_args = [str(executable), "tunnel", "--no-autoupdate", "--protocol", "http2"]
            if named_tunnel:
                origin = "https://" + public_hostname
                tunnel_args += ["run", "--token-file", str(token_file.resolve())]
            else:
                tunnel_args += ["--url", origin]
            tunnel = subprocess.Popen(tunnel_args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            processes.append(tunnel)
            discovered = []
            registered = threading.Event()
            def drain():
                for line in tunnel.stdout:
                    tunnel_log.write(line)
                    tunnel_log.flush()
                    print(line.rstrip(), flush=True)
                    if "Registered tunnel connection" in line:
                        registered.set()
                    match = re.search(r"https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com", line)
                    if match:
                        discovered.append(match[0])
            thread = threading.Thread(target=drain, daemon=True)
            thread.start()
            deadline = time.monotonic() + 50
            if named_tunnel:
                # Named tunnels do not print a random hostname. Require an actual
                # registered connector before publishing stable links.
                while not registered.is_set() and tunnel.poll() is None and time.monotonic() < deadline:
                    time.sleep(.1)
                if not registered.is_set():
                    raise RuntimeError("Named tunnel did not register. See private/public-test/tunnel.log.")
            else:
                while not discovered and tunnel.poll() is None and time.monotonic() < deadline:
                    time.sleep(.1)
            if not named_tunnel and (not discovered or tunnel.poll() is not None):
                raise RuntimeError("Quick Tunnel URL unavailable. See private/public-test/tunnel.log; no public access enabled.")
            if named_tunnel:
                # cloudflared's process remains the liveness signal. Actual DNS/
                # hostname routing is configured in the Cloudflare dashboard.
                print("Named tunnel started for " + origin, flush=True)
            else:
                origin = discovered[0]
        write_link_state(folder, status="ready", origin=origin)
        links = json.loads((folder / "links.json").read_text(encoding="utf-8"))
        print("\nTeacher: " + links["teacher"] + "\nStudent: " + links["student"], flush=True)
        print("Keys (local only): " + str(config), flush=True)
        print("Keep this program and computer running. Ctrl+C stops public access.", flush=True)
        print("Live logs below are prefixed by test space. Full logs stay in spaces/<id>/runtime.log.", flush=True)
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(create_gateway(config, workers, origin, secure=not args.local_only),
            host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False))
        def monitor():
            while not server.should_exit:
                if any(p.poll() is not None for p in processes):
                    print("A test worker/tunnel stopped; closing public access. Inspect logs.", flush=True)
                    server.should_exit = True
                    break
                for ident, offset in list(log_offsets.items()):
                    path = folder / "spaces" / ident / "runtime.log"
                    try:
                        with path.open("r", encoding="utf-8") as file:
                            file.seek(offset if path.stat().st_size >= offset else 0)
                            lines = file.read()
                            log_offsets[ident] = file.tell()
                        for line in lines.splitlines():
                            print("[" + ident + "] " + line, flush=True)
                    except (OSError, UnicodeError):
                        pass
                time.sleep(.5)
        threading.Thread(target=monitor, daemon=True).start()
        server.run()
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
        for child in processes:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for file in logs:
            file.close()
        write_link_state(folder, status="stopped")
        print("Public test stopped. Keys and saved records are retained.", flush=True)


if __name__ == "__main__":
    main()
