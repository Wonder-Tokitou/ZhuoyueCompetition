"""Shared setup/launch; never accesses production servers."""
import argparse, hashlib, os, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def run(args, cwd=ROOT):
    subprocess.run([str(x) for x in args], cwd=cwd, check=True)

def frontend_digest():
    h=hashlib.sha256()
    for base, dirs, files in os.walk(ROOT/'frontend'):
        dirs[:]=sorted(d for d in dirs if d not in {'node_modules','dist','.git'})
        for name in sorted(files):
            p=Path(base)/name
            if p.suffix=='.tsbuildinfo': continue
            h.update(p.relative_to(ROOT).as_posix().encode()); h.update(p.read_bytes())
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode', choices=['setup','local','public'])
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--local-only', action='store_true')
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,13): raise SystemExit('Use Python 3.13 (tested runtime).')
    config=ROOT/'backend/.env'
    if not config.exists(): shutil.copy2(ROOT/'backend/.env.example',config)
    python=ROOT/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists(): run([sys.executable,'-m','venv',ROOT/'.venv'])
    requirements=ROOT/'backend/requirements.txt'
    marker=ROOT/'.venv/requirements.sha256'
    expected=hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text()!=expected:
        run([python,'-m','pip','install','-r',requirements]); marker.write_text(expected)
    frontend=ROOT/'frontend'; stamp=frontend/'dist/source.sha256'; wanted=frontend_digest()
    if args.rebuild or not (frontend/'dist/index.html').exists() or not stamp.exists() or stamp.read_text()!=wanted:
        npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        if not npm: raise SystemExit('Install Node.js 24 with npm; first startup builds frontend.')
        run([npm,'ci','--ignore-scripts','--no-audit','--no-fund'],frontend)
        run([npm,'run','build'],frontend); stamp.write_text(wanted)
    if args.mode=='setup': return
    if args.mode=='public':
        if not args.local_only and not (ROOT/'tools/cloudflared.exe').exists():
            raise SystemExit('Install tools/cloudflared.exe or use --local-only. See README.md.')
        run([python,'scripts/run_public.py']+(['--local-only'] if args.local_only else []))
    else:
        run([python,'-c','from backend.app import models; from backend.app.db import Base,engine; Base.metadata.create_all(engine); from backend.app.migrations import migrate; migrate(); from backend.app.seed import main; main()'])
        run([python,'scripts/run_server.py','--no-log-window'])

if __name__=='__main__':
    try: main()
    except (subprocess.CalledProcessError,KeyboardInterrupt) as exc:
        raise SystemExit(getattr(exc,'returncode',130))
