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


def rejected(proc: subprocess.CompletedProcess, needle: str) -> None:
    if proc.returncode == 0:
        raise AssertionError(f"expected rejection containing {needle!r}, got success:\n{proc.stdout}")
    blob = proc.stdout + proc.stderr
    if needle not in blob:
        raise AssertionError(f"rejection did not contain {needle!r}:\n{blob}")


def make_zip(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("workspace/harbor_task/instruction.md", text)
        z.writestr("workspace/harbor_task/tests/grader.py", f"print({text!r})\n")


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def state(root: Path) -> dict:
    return json.loads((root / "state.json").read_text(encoding="utf-8"))


def make_gates(qa: Path, gate_ok: bool = True, shipping_ok: bool = True,
               write_gate: bool = True, write_shipping: bool = True) -> dict:
    """写机械门禁产物，返回 verdict.mechanical_checks 片段。"""
    gates = qa / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    mc: dict = {}
    if write_gate:
        write_json(gates / "current-standard.json", {
            "schema": "current-standard-gate/2",
            "decision": "PASS" if gate_ok else "FAIL",
            "final_decision_is_binary": True,
        })
        mc["current_standard_gate"] = "gates/current-standard.json"
    if write_shipping:
        write_json(gates / "shipping-limits.json", {
            "schema": "shipping-limits/1",
            "ok": shipping_ok,
            "summary": {"fail": 0 if shipping_ok else 1},
        })
        mc["shipping_limits"] = "gates/shipping-limits.json"
    return mc


def make_qa(root: Path, rnd: int, sha: str, decision: str, blocking: int = 0,
            major: int = 0, incomplete: bool = False, blocking_key: bool = False,
            gate_ok: bool = True, shipping_ok: bool = True,
            write_gate: bool = True, write_shipping: bool = True) -> Path:
    qa = root / "rounds" / f"r{rnd:02d}" / "qa"
    shard = qa / "shards" / "0001"
    write_json(shard / "findings.json", {"shard_id": "0001", "findings": [], "hotspot_answers": []})
    write_json(shard / "coverage.json", {
        "shard_id": "0001", "files_assigned": 2,
        "files_read": 1 if incomplete else 2,
        "hotspots_assigned": 1, "hotspots_answered": 1,
        "complete": not incomplete, "coverage_pct": 50.0 if incomplete else 100.0})
    mc = make_gates(qa, gate_ok=gate_ok, shipping_ok=shipping_ok,
                    write_gate=write_gate, write_shipping=write_shipping)
    findings = []
    if blocking_key:
        findings.append({"id": "F-1", "severity": "blocking", "file": "tests/grader.py",
                         "line": 1, "summary": "demo", "evidence": "demo",
                         "class": "failure_as_zero", "responsible": "expert"})
    verdict = {
        "task": "demo", "round": rnd, "input_sha256": sha, "decision": decision,
        "standard_version": "selftest",
        "blocking_count": blocking, "major_count": major, "minor_count": 0,
        "key_findings": findings,
        "mechanical_checks": mc,
        "failure_classes": {},
    }
    vp = qa / "verdict.json"
    hp = qa / "handoff.json"
    write_json(vp, verdict)
    write_json(hp, {
        "role": "qa_manager", "round": rnd, "input_sha256": sha, "status": "done",
        "execution_mode": "subagents",
        "shards": [{"id": "0001", "coverage_pct": 100.0, "complete": True, "mode": "subagent"}],
        "output_paths": [str(vp), str(hp)], "commands_run": []})
    return qa


def fresh_loop(tmp: Path, name: str, v1: Path) -> tuple[Path, dict]:
    root = tmp / name
    ok(run("init", "--task", name, "--zip", str(v1), "--root", str(root),
           "--max-rounds", "2", "--shard-count", "1"))
    return root, state(root)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopctl_selftest_") as tmp:
        tmp = Path(tmp)
        v1, v2 = tmp / "v1.zip", tmp / "v2.zip"
        make_zip(v1, "v1")
        make_zip(v2, "v2")

        # 主路径：FAIL → 修复 → PASS → done
        root, st = fresh_loop(tmp, "demo", v1)
        assert st["schema_version"] == 2 and st["phase"] == "qa" and st["round"] == 1, st

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

        qa2 = make_qa(root, 2, st["input_sha256"], "PASS")
        ok(run("record-qa", "--root", str(root), "--verdict", str(qa2 / "verdict.json"),
               "--handoff", str(qa2 / "handoff.json")))
        assert state(root)["phase"] == "done", state(root)
        ok(run("verify", "--root", str(root)))

        # 拒绝 1：handoff 输入哈希不符
        root, st = fresh_loop(tmp, "demo2", v1)
        q = make_qa(root, 1, st["input_sha256"], "FAIL", blocking=1, blocking_key=True)
        bad = tmp / "bad"
        write_json(bad / "handoff.json", {
            "role": "qa_manager", "round": 1, "input_sha256": "0" * 64,
            "status": "done", "output_paths": [], "commands_run": []})
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(bad / "handoff.json")), "handoff.input_sha256")

        # 拒绝 2：PASS 但分片未完成
        root, st = fresh_loop(tmp, "demo3", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS", incomplete=True)
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "分片未完成")

        # 拒绝 3：PASS 但 key_findings 含 blocking
        root, st = fresh_loop(tmp, "demo4", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS", blocking=1, blocking_key=True)
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "不得含 blocking")

        # 拒绝 4：旧结论 PASS_WITH_WAIVERS 已废止
        root, st = fresh_loop(tmp, "demo5", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS_WITH_WAIVERS")
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "已废止")

        # 拒绝 5：PASS 但缺机械门禁
        root, st = fresh_loop(tmp, "demo6", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS", write_gate=False)
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "current_standard_gate")

        # 拒绝 6：PASS 但门禁判 FAIL
        root, st = fresh_loop(tmp, "demo7", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS", gate_ok=False)
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "判定 FAIL")

        # 拒绝 7：PASS 但缺体积检查
        root, st = fresh_loop(tmp, "demo8", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS", write_shipping=False)
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "shipping_limits")

        # 拒绝 8：PASS 但体积检查有 fail
        root, st = fresh_loop(tmp, "demo9", v1)
        q = make_qa(root, 1, st["input_sha256"], "PASS", shipping_ok=False)
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "shipping_limits")

        # 拒绝 9：FAIL 但零 blocking
        root, st = fresh_loop(tmp, "demo10", v1)
        q = make_qa(root, 1, st["input_sha256"], "FAIL")
        rejected(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
                     "--handoff", str(q / "handoff.json")), "blocking_count=0")

        # 拒绝 10：修复包与输入同哈希
        root, st = fresh_loop(tmp, "demo11", v1)
        q = make_qa(root, 1, st["input_sha256"], "FAIL", blocking=1, blocking_key=True)
        ok(run("record-qa", "--root", str(root), "--verdict", str(q / "verdict.json"),
               "--handoff", str(q / "handoff.json")))
        write_json(root / "rounds/r01/repair/handoff.json", {
            "role": "repair", "round": 1, "input_sha256": st["input_sha256"],
            "output_zip": str(v1), "output_sha256": st["input_sha256"],
            "status": "done", "commands_run": []})
        rejected(run("record-repair", "--root", str(root), "--zip", str(v1), "--report", str(report)),
                 "SHA 相同")

        # 拒绝 11：原始输入被篡改
        root, st = fresh_loop(tmp, "demo12", v1)
        with (root / "inputs" / v1.name).open("ab") as f:
            f.write(b"tamper")
        r = run("verify", "--root", str(root))
        if r.returncode != 1:
            raise AssertionError(f"tamper should fail verify: {r.stdout}\n{r.stderr}")

    print("selftest: PASS (qa -> repair -> qa -> done; 11 negative paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
