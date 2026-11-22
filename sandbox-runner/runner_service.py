"""Trusted, command-allowlisted patch runner."""
import hashlib, os, shutil, subprocess, sys, time, uuid
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

app=FastAPI(title="AutoSRE Trusted Sandbox Runner")
ROOT=Path(os.getenv("SANDBOX_SOURCE_ROOT","/repair-target")).resolve()
TOKEN=os.getenv("SANDBOX_RUNNER_TOKEN","change-sandbox-token")

class TestRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    file_path:str=Field(pattern=r"^pricing\.py$")
    original_content:str=Field(max_length=50000)
    proposed_content:str=Field(max_length=50000)
    unified_diff:str=Field(max_length=50000)
    patch_hash:str=Field(pattern=r"^[a-f0-9]{64}$")

def run_fixed(cwd:Path,args:list[str],timeout=20):
    started=time.perf_counter()
    try:
        result=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=timeout,env={"PATH":os.environ.get("PATH",""),"PYTHONPATH":str(cwd),"PYTHONDONTWRITEBYTECODE":"1","PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"})
        return result.returncode,(result.stdout+result.stderr)[-20000:],(time.perf_counter()-started)*1000
    except subprocess.TimeoutExpired as exc:return 124,f"trusted command timed out after {timeout}s\n{exc.stdout or ''}",(time.perf_counter()-started)*1000

@app.get("/health")
def health():return {"status":"ready","isolation":"separate non-root container"}

@app.post("/v1/test")
def test_patch(payload:TestRequest,x_runner_token:str=Header(default="")):
    if x_runner_token!=TOKEN:raise HTTPException(401,"runner token required")
    if hashlib.sha256(payload.unified_diff.encode()).hexdigest()!=payload.patch_hash:raise HTTPException(409,"patch hash mismatch")
    source=ROOT/payload.file_path
    if not source.is_file() or source.is_symlink() or source.read_text(encoding="utf-8")!=payload.original_content:raise HTTPException(409,"source snapshot mismatch")
    work_root=Path(os.getenv("SANDBOX_WORK_ROOT",str(Path.cwd()/".sandbox-work")));work_root.mkdir(parents=True,exist_ok=True);work=work_root/f"autosre-{uuid.uuid4().hex}";work.mkdir();logs=[];started=time.perf_counter()
    try:
        shutil.copytree(ROOT,work/"target",dirs_exist_ok=True); target=work/"target"
        baseline_code,baseline_log,_=run_fixed(target,[sys.executable,"-m","pytest","-q"],20); logs.append(f"BASELINE exit={baseline_code}\n{baseline_log}")
        (target/payload.file_path).write_text(payload.proposed_content,encoding="utf-8")
        syntax_code,syntax_log,_=run_fixed(target,[sys.executable,"-m","compileall","-q","."],10); logs.append(f"SYNTAX exit={syntax_code}\n{syntax_log}")
        test_code,test_log,_=run_fixed(target,[sys.executable,"-m","pytest","-q"],20); logs.append(f"PATCHED exit={test_code}\n{test_log}")
        passed=baseline_code!=0 and syntax_code==0 and test_code==0
        return {"status":"sandbox_passed" if passed else "sandbox_failed","passed":passed,"exit_code":test_code if syntax_code==0 else syntax_code,"baseline_exit_code":baseline_code,"duration_ms":(time.perf_counter()-started)*1000,"output":"\n\n".join(logs),"patch_hash":payload.patch_hash,"commands":["python -m pytest -q","python -m compileall -q ."]}
    finally:shutil.rmtree(work,ignore_errors=True)

