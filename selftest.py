#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loopctl.py 的自测：临时目录跑完 QA → 修复 → QA → PASS，并验证拒绝路径。

    python selftest.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOOPCTL = HERE / "loopctl.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(LOOPCTL), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def ok(proc: subprocess.CompletedProcess) -> None:
    if proc.returncode != 0:
        raise AssertionError(f"rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}")


def make_zip(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("workspace/harbor_task/instruction.md", text)
        z.writestr("workspace/harbor_task/tests/grader.py", f"print({text!r})\n")


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def state(root: Path) -> dict:
    return json.loads((root / "state.json").read_text(encoding="utf-8"))


def make_qa(root: Path, rnd: int, sha: str, decision: str, blocking: int = 0,
            major: int = 0, incomplete: bool = False, blocking_key: bool = False) -> Path:
    qa = root / "rounds" / f"r{rnd:02d}" / "qa"
    shard = qa / "shards" / "0001"
    write_json(shard / "findings.json", {"shard_id": "0001", "findings": [], "hotspot_answers": []})
    write_json(shard / "coverage.json", {
        "shard_id": "0001", "files_assigned": 2,
        "files_read": 1 if incomplete else 2,
        "hotspots_assigned": 1, "hotspots_answered": 1,
        "complete": not incomplete, "coverage_pct": 50.0 if incomplete else 100.0})
    for name in ("platform-rules", "delivery-contract", "execution-path"):
        write_json(qa / "gates" / f"{name}.json", {"ok": True})
    findings = []
    if blocking_key:
        findings.append({"id": "F-1", "severity": "blocking", "file": "tests/grader.py",
                         "line": 1, "summary": "demo", "evidence": "demo"})
    verdict = {
        "task": "demo", "round": rnd, "input_sha256": sha, "decision": decision,
        "blocking_count": blocking, "major_count": major, "minor_count": 0,
        "key_findings": findings,
        "mechanical_checks": {
            "platform_rules": "gates/platform-rules.json",
            "delivery_contract": "gates/delivery-contract.json",
            "execution_path": "gates/execution-path.json"}}
    vp = qa / "verdict.json"
    hp = qa / "handoff.json"
    write_json(vp, verdict)
    write_json(hp, {
        "role": "qa_manager", "round": rnd, "input_sha256": sha, "status": "done",
        "execution_mode": "subagents",
        "shards": [{"id": "0001", "coverage_pct": 100.0, "complete": True, "mode": "subagent"}],
        "output_paths": [str(vp), str(hp)], "commands_run": []})
    return qa


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopctl_selftest_") as tmp:
        tmp = Path(tmp)
        v1, v2 = tmp / "v1.zip", tmp / "v2.zip"
        make_zip(v1, "v1")
        make_zip(v2, "v2")
        root = tmp / "loop"
        ok(run("init", "--task", "demo", "--zip", str(v1), "--root", str(root),
               "--max-rounds", "2", "--shard-count", "1"))
        st = state(root)
        assert st["phase"] == "qa" and st["round"] == 1, st

        qa1 = make_qa(root, 1, st["input_sha256"], "FAIL", blocking=1, blocking_key=True)
        ok(run("record-qa", "--root", str(root), "--verdict", str(qa1 / "verdict.json"),
               "--handoff", str(qa1 / "handoff.json")))
        assert state(root)["phase"] == "repair", state(root)

        report = root / "rounds/r01/repair/report.md"
        report.write_text("fixed\n", encoding="utf-8")
        v2_sha = hashlib.sha256(v2.read_bytes()).hexdigest()
        write_json(root / "rounds/r01/repair/handoff.json", {
            "role": "repair", "round": 1, "input_sha256": st["input_sha256"],
            "output_zip": str(v2), "output_sha256": v2_sha,
            "status": "done", "commands_run": []})
        ok(run("record-repair", "--root", str(root), "--zip", str(v2), "--report", str(report)))
        st = state(root)
        assert st["phase"] == "qa" and st["round"] == 2, st

        qa2 = make_qa(root, 2, st["input_sha256"], "PASS_WITH_WAIVERS")
        ok(run("record-qa", "--root", str(root), "--verdict", str(qa2 / "verdict.json"),
               "--handoff", str(qa2 / "handoff.json")))
        assert state(root)["phase"] == "done", state(root)
        ok(run("verify", "--root", str(root)))

        # 拒绝 1：handoff 输入哈希不符
        root2 = tmp / "loop2"
        ok(run("init", "--task", "demo2", "--zip", str(v1), "--root", str(root2)))
        st2 = state(root2)
        q2 = make_qa(root2, 1, st2["input_sha256"], "FAIL", blocking=1, blocking_key=True)
        bad = tmp / "bad"
        write_json(bad / "handoff.json", {
            "role": "qa_manager", "round": 1, "input_sha256": "0" * 64,
            "status": "done", "output_paths": [], "commands_run": []})
        r = run("record-qa", "--root", str(root2), "--verdict", str(q2 / "verdict.json"),
                "--handoff", str(bad / "handoff.json"))
        assert r.returncode == 2, r

        # 拒绝 2：PASS 但分片未完成
        root3 = tmp / "loop3"
        ok(run("init", "--task", "demo3", "--zip", str(v1), "--root", str(root3)))
        st3 = state(root3)
        q3 = make_qa(root3, 1, st3["input_sha256"], "PASS", incomplete=True)
        r = run("record-qa", "--root", str(root3), "--verdict", str(q3 / "verdict.json"),
                "--handoff", str(q3 / "handoff.json"))
        assert r.returncode == 2, r

        # 拒绝 3：PASS 但 key_findings 含 blocking
        root4 = tmp / "loop4"
        ok(run("init", "--task", "demo4", "--zip", str(v1), "--root", str(root4)))
        st4 = state(root4)
        q4 = make_qa(root4, 1, st4["input_sha256"], "PASS", blocking_key=True)
        r = run("record-qa", "--root", str(root4), "--verdict", str(q4 / "verdict.json"),
                "--handoff", str(q4 / "handoff.json"))
        assert r.returncode == 2, r

        # 拒绝 4：修复包与输入同哈希
        root5 = tmp / "loop5"
        ok(run("init", "--task", "demo5", "--zip", str(v1), "--root", str(root5)))
        st5 = state(root5)
        q5 = make_qa(root5, 1, st5["input_sha256"], "FAIL", blocking=1, blocking_key=True)
        ok(run("record-qa", "--root", str(root5), "--verdict", str(q5 / "verdict.json"),
               "--handoff", str(q5 / "handoff.json")))
        write_json(root5 / "rounds/r01/repair/handoff.json", {
            "role": "repair", "round": 1, "input_sha256": st5["input_sha256"],
            "output_zip": str(v1), "output_sha256": st5["input_sha256"],
            "status": "done", "commands_run": []})
        r = run("record-repair", "--root", str(root5), "--zip", str(v1), "--report", str(report))
        assert r.returncode == 2, r

        # 拒绝 5：原始输入被篡改
        root6 = tmp / "loop6"
        ok(run("init", "--task", "demo6", "--zip", str(v1), "--root", str(root6)))
        with (root6 / "inputs" / v1.name).open("ab") as f:
            f.write(b"tamper")
        r = run("verify", "--root", str(root6))
        assert r.returncode == 1, r

    print("selftest: PASS (qa -> repair -> qa -> done; 5 negative paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
