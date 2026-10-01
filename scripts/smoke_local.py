"""Smoke-check only the exported copy, with its own disposable database."""
from pathlib import Path
import os, socket, subprocess, sys, time
import httpx

root=Path(__file__).resolve().parents[1]
scratch=root/'.test-http-export'; scratch.mkdir(exist_ok=True)
env={**os.environ,'PYTHONUTF8':'1','DATABASE_URL':'sqlite:///'+(scratch/'app.db').as_posix(),
     'ARTIFACT_ROOT':str(scratch/'artifacts'),'CASE_SIM_STATIC_DIR':str(scratch/'static'),
     'CASE_SIM_LOG_FILE':str(scratch/'runtime.log'),'TEACHER_PASSWORD':'export-smoke-only',
     'LLM_API_KEY':'','DEEPSEEK_API_KEY':'','TAVILY_API_KEY':'','PUBLIC_BASE_URL':''}
subprocess.run([sys.executable,'-c','from backend.app import models; from backend.app.db import Base,engine; Base.metadata.create_all(engine); from backend.app.migrations import migrate; migrate(); from backend.app.seed import main; main()'],cwd=root,env=env,check=True)
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
with (scratch/'process.log').open('w',encoding='utf-8') as log:
 p=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(port)],cwd=root,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
 try:
  with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=2) as c:
   for _ in range(100):
    if p.poll() is not None:raise RuntimeError('Server exited; inspect process.log')
    try:
     if c.get('/api/health').status_code==200:break
    except httpx.HTTPError:pass
    time.sleep(.1)
   else:raise RuntimeError('Server readiness timeout')
   for route in ['/teacher/login','/student','/student/benchmark-demo-v4']:
    r=c.get(route);assert r.status_code==200 and '<html' in r.text,(route,r.status_code)
   assets=list((root/'frontend/dist/assets').glob('*.js'));assert assets
   assert c.get('/assets/'+assets[0].name).status_code==200
   assert c.get('/api/not-existing-export-check').status_code==404
   r=c.post('/api/teacher/login',json={'password':'export-smoke-only'})
   assert r.status_code==200,(r.status_code,r.text)
   print('PASS: local port, health, three SPA routes, JS asset, API 404, teacher login; isolated SQLite; no Aliyun requests')
 finally:
  p.terminate()
  try:p.wait(timeout=10)
  except subprocess.TimeoutExpired:p.kill();p.wait()
