#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loopctl.py —— 质检/修复循环的磁盘状态机。

只做四件事：建目录、发下一阶段的提示词、收产物并校验、推进/终止循环。
不执行质检、不执行修复、不读写题包内容；所有重活交给被派发的 agent。

用法：
    python loopctl.py init   --task auto2768 --zip <包.zip> [--root <循环根>] [--max-rounds 3]
    python loopctl.py next
    python loopctl.py status [--json]
    python loopctl.py record-qa    --verdict <verdict.json> --handoff <handoff.json>
    python loopctl.py record-repair --zip <新包.zip> --report <report.md>
    python loopctl.py stop --reason "..."
    python loopctl.py verify

目录约定（init 自动创建）：
    <root>/
      inputs/<包.zip>                     # 只读副本
      rounds/rNN/qa/{workspace,shards,gates,coverage,official}
      rounds/rNN/repair/{src,out}
      state.json
      claims/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_SKILL = r"D:\Desktop\论文最新包\adversarial-engineering-qa-v2.3.1\adversarial-engineering-qa"
PASS_DECISIONS = {"PASS", "PASS_WITH_WAIVERS"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def state_path(root: Path) -> Path:
    return root / "state.json"


def load_state(root: Path) -> dict:
    p = state_path(root)
    if not p.is_file():
        die(f"未找到 state.json：{p}（先运行 init）")
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(root: Path, st: dict) -> None:
    st["updated_at"] = now()
    state_path(root).write_text(
        json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def die(msg: str) -> None:
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(2)


def round_dir(root: Path, rnd: int) -> Path:
    return root / "rounds" / f"r{rnd:02d}"


def ensure_round(root: Path, rnd: int) -> None:
    base = round_dir(root, rnd)
    for sub in ("qa/workspace", "qa/shards", "qa/gates", "qa/coverage", "qa/official",
                "repair/src", "repair/out"):
        (base / sub).mkdir(parents=True, exist_ok=True)


def fill(template: str, mapping: dict) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def prompt_path(root: Path, name: str) -> Path:
    p = root / "prompts" / name
    if not p.is_file():
        die(f"缺少提示词模板：{p}")
    return p


def write_template_tree(root: Path) -> None:
    """把本文件同目录的 prompts/ 与 schemas/ 复制进循环根，保证闭环自包含。"""
    here = Path(__file__).resolve().parent
    for sub in ("prompts", "schemas"):
        src = here / sub
        dst = root / sub
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)


# --------------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace) -> None:
    zip_path = Path(args.zip).resolve()
    if not zip_path.is_file():
        die(f"输入包不存在：{zip_path}")
    root = Path(args.root).resolve() if args.root else (
        Path(r"D:\Desktop\论文最新包\loop") / args.task)
    if state_path(root).exists() and not args.force:
        die(f"{root} 已有 state.json；确需重建请加 --force")
    root.mkdir(parents=True, exist_ok=True)
    write_template_tree(root)
    (root / "inputs").mkdir(exist_ok=True)
    (root / "claims").mkdir(exist_ok=True)

    dst = root / "inputs" / zip_path.name
    if not dst.exists():
        if args.link:
            try:
                os.link(zip_path, dst)
            except OSError:
                shutil.copy2(zip_path, dst)
        else:
            shutil.copy2(zip_path, dst)
    digest = sha256_file(dst)
    ensure_round(root, 1)
    st = {
        "schema_version": SCHEMA_VERSION,
        "task": args.task,
        "root": str(root),
        "skill_dir": args.skill_dir or DEFAULT_SKILL,
        "max_rounds": args.max_rounds,
        "shard_count": args.shard_count,
        "round": 1,
        "phase": "qa",
        "input_zip": str(dst),
        "input_sha256": digest,
        "original_zip": str(zip_path),
        "original_sha256": sha256_file(zip_path),
        "history": [],
        "stop_reason": None,
        "created_at": now(),
        "updated_at": now(),
    }
    save_state(root, st)
    print(f"initialized: {root}")
    print(f"input: {dst}")
    print(f"sha256: {digest}")
    print("next: python loopctl.py next")


# --------------------------------------------------------------------------- next
def cmd_next(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else Path(load_state_root())
    st = load_state(root)
    mapping = {
        "TASK": st["task"],
        "ROUND": st["round"],
        "MAX_ROUNDS": st["max_rounds"],
        "INPUT_ZIP": st["input_zip"],
        "INPUT_SHA": st["input_sha256"],
        "QA_DIR": str(round_dir(root, st["round"]) / "qa"),
        "REPAIR_DIR": str(round_dir(root, st["round"]) / "repair"),
        "SHARD_COUNT": st["shard_count"],
        "SKILL_DIR": st["skill_dir"],
        "ROOT": str(root),
        "VERDICT_PATH": str(round_dir(root, st["round"]) / "qa" / "verdict.json"),
        "HANDOFF_PATH": str(round_dir(root, st["round"]) / "qa" / "handoff.json"),
        "NEW_ZIP": str(round_dir(root, st["round"]) / "repair" / "out" / f"{st['task']}_round{st['round']}_fixed.zip"),
        "REPORT_PATH": str(round_dir(root, st["round"]) / "repair" / "report.md"),
    }
    phase = st["phase"]
    if phase == "qa":
        tpl = prompt_path(root, "01-qa-manager.md").read_text(encoding="utf-8")
        print(fill(tpl, mapping))
    elif phase == "repair":
        tpl = prompt_path(root, "03-repair-agent.md").read_text(encoding="utf-8")
        print(fill(tpl, mapping))
    elif phase in ("done", "stopped"):
        print(f"loop {phase}: {st.get('stop_reason')}")
        print(f"state: {state_path(root)}")
    else:
        die(f"未知 phase：{phase}")


def load_state_root() -> str:
    # 未显式给 --root 时，从 cwd 向上找 state.json
    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        if (p / "state.json").is_file():
            return str(p)
    die("当前目录不在循环根内，且未提供 --root")
    return ""


# --------------------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else Path(load_state_root())
    st = load_state(root)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return
    print(f"task        {st['task']}")
    print(f"root        {root}")
    print(f"round       {st['round']} / {st['max_rounds']}")
    print(f"phase       {st['phase']}")
    print(f"input       {st['input_zip']}")
    print(f"input_sha   {st['input_sha256'][:16]}…")
    if st.get("stop_reason"):
        print(f"stop        {st['stop_reason']}")
    print("history")
    for h in st["history"]:
        if h["phase"] == "qa":
            print(f"  r{h['round']:02d} qa      {h.get('decision'):<18} blocking={h.get('blocking')} major={h.get('major')}")
        else:
            print(f"  r{h['round']:02d} repair  -> {Path(h.get('output_zip','')).name}  {str(h.get('output_sha256',''))[:16]}…")


# --------------------------------------------------------------------------- record-qa
def _load_json(path: Path, label: str) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die(f"{label} 无法解析：{exc}")
    if not isinstance(obj, dict):
        die(f"{label} 必须是 JSON 对象")
    return obj


def _resolve_path(base: Path, value: str) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else base / p).resolve()


def _validate_shards(qa_dir: Path, decision: str) -> list[dict]:
    shards_dir = qa_dir / "shards"
    if not shards_dir.is_dir():
        die(f"缺少分片目录：{shards_dir}")
    dirs = sorted(p for p in shards_dir.iterdir() if p.is_dir())
    if not dirs:
        die("分片目录为空：本轮 QA 没有任何分片产物")
    rows, incomplete = [], []
    for d in dirs:
        cov_p, find_p = d / "coverage.json", d / "findings.json"
        if not cov_p.is_file():
            die(f"分片 {d.name} 缺少 coverage.json")
        if not find_p.is_file():
            die(f"分片 {d.name} 缺少 findings.json")
        cov = _load_json(cov_p, f"{d.name}/coverage.json")
        _load_json(find_p, f"{d.name}/findings.json")
        complete = bool(cov.get("complete"))
        pct = float(cov.get("coverage_pct", 100.0 if complete else 0.0))
        rows.append({"id": d.name, "coverage_pct": pct, "complete": complete})
        if not complete or pct < 100.0:
            incomplete.append(d.name)
    if decision in PASS_DECISIONS and incomplete:
        die("decision=PASS* 但以下分片未完成：" + ", ".join(incomplete))
    return rows


def _validate_mechanical(verdict: dict, qa_dir: Path) -> None:
    mc = verdict.get("mechanical_checks")
    if not isinstance(mc, dict):
        die("verdict 缺少 mechanical_checks（三组机械门禁的产物路径）")
    for key in ("platform_rules", "delivery_contract", "execution_path"):
        value = mc.get(key)
        if not isinstance(value, str) or not value.strip():
            die(f"mechanical_checks.{key} 缺失")
        if not _resolve_path(qa_dir, value).is_file():
            die(f"mechanical_checks.{key} 指向的文件不存在：{value}")


def _blocking_signatures(verdict: dict) -> list[str]:
    sig = []
    for f in verdict.get("key_findings") or []:
        if str(f.get("severity", "")).lower() == "blocking":
            sig.append(f"{f.get('file', '?')}:{f.get('line', '?')}:{str(f.get('summary', ''))[:80]}")
    return sorted(sig)


def cmd_record_qa(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else Path(load_state_root())
    st = load_state(root)
    if st["phase"] != "qa":
        die(f"当前 phase={st['phase']}，不接收 QA 产物")
    verdict_path = Path(args.verdict).resolve()
    handoff_path = Path(args.handoff).resolve()
    if not verdict_path.is_file():
        die(f"verdict 不存在：{verdict_path}")
    if not handoff_path.is_file():
        die(f"handoff 不存在：{handoff_path}")
    verdict = _load_json(verdict_path, "verdict.json")
    handoff = _load_json(handoff_path, "handoff.json")

    if verdict.get("input_sha256") != st["input_sha256"]:
        die("verdict.input_sha256 与当前输入不符")
    if handoff.get("input_sha256") != st["input_sha256"]:
        die("handoff.input_sha256 与当前输入不符：产物可能来自别的包")
    if int(verdict.get("round", st["round"])) != int(st["round"]):
        die(f"verdict.round={verdict.get('round')} 与当前 round={st['round']} 不符")
    if handoff.get("status") not in ("done", "incomplete"):
        die(f"handoff.status={handoff.get('status')}，QA 尚未收尾")

    outs = handoff.get("output_paths") or []
    if not outs:
        die("handoff.output_paths 为空：至少列出 verdict 与 handoff 自身")
    for item in outs:
        if not isinstance(item, str) or not _resolve_path(handoff_path.parent, item).exists():
            die(f"handoff.output_paths 指向不存在的文件：{item}")

    decision = str(verdict.get("decision") or verdict.get("overall") or "").upper()
    if not decision:
        die("verdict 缺少 decision/overall 字段")
    blocking = int(verdict.get("blocking_count", verdict.get("blocking", 0)) or 0)
    major = int(verdict.get("major_count", verdict.get("major", 0)) or 0)
    minor = int(verdict.get("minor_count", verdict.get("minor", 0)) or 0)

    signatures = _blocking_signatures(verdict)
    if signatures and blocking == 0:
        die("key_findings 含 blocking，但 blocking_count=0")
    if decision in PASS_DECISIONS and (blocking > 0 or signatures):
        die("decision=PASS* 不得含 blocking")
    if decision == "PASS" and major > 0:
        die("decision=PASS 不得含 major；有 major 应记 PASS_WITH_WAIVERS")

    qa_dir = verdict_path.parent
    shard_rows = _validate_shards(qa_dir, decision)
    _validate_mechanical(verdict, qa_dir)

    entry = {
        "round": st["round"], "phase": "qa", "decision": decision,
        "blocking": blocking, "major": major, "minor": minor,
        "blocking_signatures": signatures, "shards": shard_rows,
        "verdict_path": str(verdict_path), "handoff_path": str(handoff_path),
        "ts": now(),
    }
    st["history"].append(entry)
    passed = decision in PASS_DECISIONS and blocking == 0
    prev_qa = [h for h in st["history"][:-1] if h.get("phase") == "qa"]
    repeated = bool(signatures and prev_qa and prev_qa[-1].get("blocking_signatures") == signatures)
    if passed:
        st["phase"] = "done"
        st["stop_reason"] = f"pass: {decision} (round {st['round']})"
    elif repeated:
        st["phase"] = "stopped"
        st["stop_reason"] = "no_progress: 同一组 blocking 连续两轮未变"
    elif st["round"] >= st["max_rounds"]:
        st["phase"] = "stopped"
        st["stop_reason"] = f"max_rounds={st['max_rounds']} reached without pass"
    else:
        st["phase"] = "repair"
    save_state(root, st)
    print(f"recorded qa r{st['round']:02d}: {decision}, blocking={blocking}, major={major}")
    print(f"shards: {', '.join(r['id'] for r in shard_rows)}")
    print(f"phase -> {st['phase']}" + (f" ({st['stop_reason']})" if st.get("stop_reason") else ""))


# --------------------------------------------------------------------------- record-repair
def cmd_record_repair(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else Path(load_state_root())
    st = load_state(root)
    if st["phase"] != "repair":
        die(f"当前 phase={st['phase']}，不接收修复产物")
    new_zip = Path(args.zip).resolve()
    report = Path(args.report).resolve()
    if not new_zip.is_file():
        die(f"修复包不存在：{new_zip}")
    if not report.is_file():
        die(f"修复报告不存在：{report}")
    digest = sha256_file(new_zip)
    if digest == st["input_sha256"]:
        die("修复包与输入包 SHA 相同，未产生任何变更")
    repair_dir = round_dir(root, st["round"]) / "repair"
    repair_handoff = repair_dir / "handoff.json"
    if not repair_handoff.is_file():
        die(f"修复代理未写交接单：{repair_handoff}")
    reph = _load_json(repair_handoff, "repair/handoff.json")
    if reph.get("input_sha256") != st["input_sha256"]:
        die("repair/handoff.input_sha256 与当前输入不符")
    if reph.get("output_sha256") and reph["output_sha256"] != digest:
        die("repair/handoff.output_sha256 与实际修复包不符")
    if reph.get("status") not in ("done", "incomplete"):
        die(f"repair/handoff.status={reph.get('status')}，修复尚未收尾")
    out_dir = repair_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / new_zip.name
    if new_zip.resolve() != dst.resolve():
        shutil.copy2(new_zip, dst)
    st["history"].append({
        "round": st["round"], "phase": "repair",
        "output_zip": str(dst), "output_sha256": digest,
        "report": str(report), "repair_handoff": str(repair_handoff),
        "repair_status": reph.get("status"), "ts": now(),
    })
    nxt = st["round"] + 1
    if nxt > st["max_rounds"]:
        st["phase"] = "stopped"
        st["stop_reason"] = f"max_rounds={st['max_rounds']} reached"
        save_state(root, st)
        print(f"recorded repair r{st['round']:02d} -> {dst.name}")
        print(f"phase -> stopped ({st['stop_reason']})")
        return
    st["round"] = nxt
    st["phase"] = "qa"
    st["input_zip"] = str(dst)
    st["input_sha256"] = digest
    st["stop_reason"] = None
    ensure_round(root, nxt)
    save_state(root, st)
    print(f"recorded repair r{st['round'] - 1:02d} -> {dst.name}")
    print(f"next input: {dst}")
    print(f"sha256: {digest}")
    print(f"phase -> qa (round {nxt})")


# --------------------------------------------------------------------------- stop / verify
def cmd_stop(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else Path(load_state_root())
    st = load_state(root)
    st["phase"] = "stopped"
    st["stop_reason"] = args.reason
    save_state(root, st)
    print(f"stopped: {args.reason}")


def cmd_verify(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve() if args.root else Path(load_state_root())
    st = load_state(root)
    failures = 0

    p = Path(st["input_zip"])
    if not p.is_file():
        die(f"当前输入包不存在：{p}")
    got = sha256_file(p)
    ok = got == st["input_sha256"]
    print(f"input      {p}")
    print(f"declared   {st['input_sha256']}")
    print(f"actual     {got}")
    print("RESULT     " + ("OK" if ok else "MISMATCH"))
    failures += 0 if ok else 1

    orig = root / "inputs" / Path(st["original_zip"]).name
    if not orig.is_file():
        print(f"original   {orig}  MISSING")
        failures += 1
    else:
        orig_got = sha256_file(orig)
        orig_ok = orig_got == st["original_sha256"]
        print(f"original   {orig_got}  " + ("OK" if orig_ok else "MISMATCH"))
        failures += 0 if orig_ok else 1

    for h in st["history"]:
        if h.get("phase") != "repair":
            continue
        zp = Path(h["output_zip"])
        if not zp.is_file():
            print(f"repair r{h['round']:02d}  {zp.name}  MISSING")
            failures += 1
            continue
        good = sha256_file(zp) == h["output_sha256"]
        print(f"repair r{h['round']:02d}  {zp.name}  " + ("OK" if good else "MISMATCH"))
        failures += 0 if good else 1

    for sub in ("qa/workspace", "qa/shards", "repair/src", "repair/out"):
        d = round_dir(root, st["round"]) / sub
        print(f"dir        {sub:<16} {'ok' if d.is_dir() else 'MISSING'}")
    if failures:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="质检/修复循环状态机")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="初始化循环目录")
    p.add_argument("--task", required=True)
    p.add_argument("--zip", required=True)
    p.add_argument("--root", default=None)
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--shard-count", type=int, default=4)
    p.add_argument("--skill-dir", default=None)
    p.add_argument("--link", action="store_true", help="优先硬链接输入包，失败再复制")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    for name, fn in (("next", cmd_next), ("status", cmd_status), ("verify", cmd_verify)):
        q = sub.add_parser(name)
        q.add_argument("--root", default=None)
        if name == "status":
            q.add_argument("--json", action="store_true")
        q.set_defaults(func=fn)

    q = sub.add_parser("record-qa")
    q.add_argument("--root", default=None)
    q.add_argument("--verdict", required=True)
    q.add_argument("--handoff", required=True)
    q.set_defaults(func=cmd_record_qa)

    q = sub.add_parser("record-repair")
    q.add_argument("--root", default=None)
    q.add_argument("--zip", required=True)
    q.add_argument("--report", required=True)
    q.set_defaults(func=cmd_record_repair)

    q = sub.add_parser("stop")
    q.add_argument("--root", default=None)
    q.add_argument("--reason", required=True)
    q.set_defaults(func=cmd_stop)
    return ap


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
