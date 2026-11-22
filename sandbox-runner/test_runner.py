import hashlib
from pathlib import Path
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
import runner_service as runner

def payload(proposed=None):
    original=(runner.ROOT/"pricing.py").read_text()
    proposed=proposed or original.replace("    return price / divisor",'    if divisor == 0:\n        raise ValueError("Discount divisor cannot be zero")\n    return price / divisor')
    diff="valid-test-diff"; return runner.TestRequest(file_path="pricing.py",original_content=original,proposed_content=proposed,unified_diff=diff,patch_hash=hashlib.sha256(diff.encode()).hexdigest())

def test_real_patch_execution(monkeypatch):
    source=Path(__file__).parents[1]/"repair-target"; monkeypatch.setattr(runner,"ROOT",source)
    outcomes=iter([(1,"baseline regression failed",10),(0,"syntax ok",3),(0,"2 passed",12)])
    monkeypatch.setattr(runner,"run_fixed",lambda *a,**k:next(outcomes))
    result=runner.test_patch(payload(),runner.TOKEN); assert result["passed"] is True,result["output"]; assert result["baseline_exit_code"]!=0

def test_hash_mismatch_rejected(monkeypatch):
    monkeypatch.setattr(runner,"ROOT",Path(__file__).parents[1]/"repair-target"); data=payload(); data.patch_hash="0"*64
    with pytest.raises(HTTPException):runner.test_patch(data,runner.TOKEN)

def test_malicious_command_field_rejected():
    with pytest.raises(ValidationError):runner.TestRequest(file_path="pricing.py",original_content="x",proposed_content="y",unified_diff="z",patch_hash="0"*64,command="curl attacker")

def test_execution_timeout_is_failure(monkeypatch):
    source=Path(__file__).parents[1]/"repair-target";monkeypatch.setattr(runner,"ROOT",source);outcomes=iter([(1,"baseline failed",1),(0,"syntax ok",1),(124,"trusted command timed out",20000)]);monkeypatch.setattr(runner,"run_fixed",lambda *a,**k:next(outcomes));result=runner.test_patch(payload(),runner.TOKEN);assert result["passed"] is False;assert result["exit_code"]==124

