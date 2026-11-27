#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""base_watch.py —— 盯住飞书出题表的「静态质检结果 + 终检结果」，变更时 bot 私聊告警。

目标（2026-11-25 用户确认）：
  三期出题表 base 经环境变量 WATCH_BASE_TOKEN 注入（或 --base 参数），表 ID 经 WATCH_TABLES 注入（或 --tables 参数）
    - 三期领题表：auto0340 / auto2001 / auto2768
    - 三期专家自行选题表：AutoRe0823
  字段：静态质检结果 / 静态质检状态 / 质检环节 / 终检结果 / 终检不通过原因 / 返修状态 等。

为什么轮询：飞书 CLI 事件系统没有 Base 记录变更事件（只有 IM/审批/任务等），
所以用 manifest.rev 比对 + 字段 diff；只在真变化时推送，不变化完全静默。

读表用用户身份，发消息用 bot 身份，均走本机 lark-cli，不存 token。

用法：
    python scripts/base_watch.py --state <state.json> snapshot     # 建基线，不告警
    python scripts/base_watch.py --state <state.json> check [--send]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_BASE = os.environ.get("WATCH_BASE_TOKEN", "")
DEFAULT_TABLES = os.environ.get("WATCH_TABLES", "")  # 逗号分隔：领题表,自行选题表
DEFAULT_USER = os.environ.get("WATCH_USER_ID", "")
TARGET_TASKS = ("auto0340", "auto2001", "auto2768", "auto0823")

# 只盯这些字段：静态/终检结果与状态、返修、打回原因、环节、轮次
WATCH_FIELDS = (
    "当前质检轮次", "质检环节", "领取状态",
    "静态质检状态", "静态质检结果", "静态质检未通过理由", "静态质检返修状态",
    "终检结果", "终检不通过原因", "终检打回时间",
    "返修状态", "是否完成", "是否入库", "申诉回复",
)
FIELD_LABEL = {
    "当前质检轮次": "轮次", "质检环节": "环节", "领取状态": "领取",
    "静态质检状态": "静态状态", "静态质检结果": "静态结果",
    "静态质检未通过理由": "静态未过理由", "静态质检返修状态": "静态返修",
    "终检结果": "终检结果", "终检不通过原因": "终检原因",
    "终检打回时间": "终检打回", "返修状态": "返修状态",
    "是否完成": "完成", "是否入库": "入库", "申诉回复": "申诉回复",
}


def lark(*args: str, as_user: str = "user", expect_envelope: bool = True) -> dict:
    cmd = ["lark-cli", *args, "--as", as_user, "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"lark-cli 输出无法解析: {exc}\n{proc.stdout[:400]}\n{proc.stderr[:400]}") from exc
    if proc.returncode != 0:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise RuntimeError(f"lark-cli 调用失败 (rc={proc.returncode}): "
                           f"{err.get('message', proc.stderr[:300])}")
    if expect_envelope and data.get("ok") is not True:
        err = data.get("error", {})
        raise RuntimeError(f"lark-cli 业务失败: {err.get('message', str(data)[:300])}")
    return data


def normalize(value) -> str:
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        return "/".join(str(x) for x in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).replace("\n", " ")


def task_name(value) -> str:
    """把 AutoRe0823 / autore0823 / auto0823 都归一成 auto0823。"""
    s = str(value or "").strip().lower().replace("autore", "auto")
    return s


def fetch_snapshot(base: str, tables: list[str]) -> dict:
    """拉所有表，只保留4道目标题的质检字段；rev 取各表 rev 的组合。"""
    rows: dict[str, dict] = {}
    revs: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="base_watch_") as td:
        for tbl in tables:
            out = str(Path(td) / f"{tbl}.ndjson")
            manifest = lark("base", "+record-list", "--base-token", base,
                            "--table-id", tbl, "--format", "ndjson",
                            "--output", out, "--overwrite", expect_envelope=False)
            revs[tbl] = int(manifest.get("rev") or 0)
            for line in Path(out).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                raw = rec.get("题号（例如AutoRe0001）") or rec.get("题号") or ""
                name = task_name(raw)
                if name not in TARGET_TASKS:
                    continue
                # 两道题号写法不同：归一展示，但保留原值以防混淆
                rows[name] = {
                    "_display": str(raw),
                    "_table": tbl,
                    **{k: normalize(rec.get(k)) for k in WATCH_FIELDS if k in rec},
                }
    return {"revs": revs, "rows": rows, "fetched_at": now()}


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def diff(old: dict, new: dict) -> list[str]:
    old_rows = old.get("rows", {})
    new_rows = new.get("rows", {})
    lines: list[str] = []
    for task in TARGET_TASKS:
        before = old_rows.get(task)
        after = new_rows.get(task)
        if before is None and after is None:
            continue
        label_task = (after or before).get("_display", task)
        if before is None:
            lines.append(f"🆕 **{label_task}** 出现在质检表中")
            continue
        if after is None:
            lines.append(f"❌ **{label_task}** 从质检表中消失")
            continue
        changes = []
        for key in WATCH_FIELDS:
            b = before.get(key, "")
            a = after.get(key, "")
            if b != a:
                # 终检原因可能很长，截断到 200 字符避免刷屏
                bb = b if len(b) <= 200 else b[:200] + "…"
                aa = a if len(a) <= 200 else a[:200] + "…"
                changes.append(f"  - {FIELD_LABEL.get(key, key)}: `{bb or '空'}` → `{aa or '空'}`")
        if changes:
            lines.append(f"🔄 **{label_task}**")
            lines.extend(changes)
    return lines


def rev_summary(old: dict, new: dict) -> str:
    parts = []
    for tbl in sorted(set(old.get("revs", {})) | set(new.get("revs", {}))):
        o = old.get("revs", {}).get(tbl)
        n = new.get("revs", {}).get(tbl)
        parts.append(f"{tbl[-6:]}:{o}→{n}")
    return ", ".join(parts)


def load_state(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def send_message(text: str, user: str) -> dict:
    return lark("im", "+messages-send", "--user-id", user,
                "--text", text, as_user="bot")


def main() -> int:
    ap = argparse.ArgumentParser(description="4 题质检结果变更 → bot 私聊告警")
    ap.add_argument("action", choices=("snapshot", "check"))
    ap.add_argument("--state", required=True)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--tables", default=DEFAULT_TABLES, help="逗号分隔的表 ID")
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()
    missing = [name for name, val in (
        ("--base/WATCH_BASE_TOKEN", args.base),
        ("--tables/WATCH_TABLES", args.tables),
        ("--user/WATCH_USER_ID", args.user)) if not val]
    if missing:
        ap.error("缺少必填参数（命令行参数或环境变量）: " + ", ".join(missing))

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    new = fetch_snapshot(args.base, tables)
    old = load_state(Path(args.state).resolve())

    if args.action == "snapshot" or old is None:
        save_state(Path(args.state).resolve(), new)
        print(f"baseline: revs={new['revs']} tasks={sorted(new['rows'])}")
        return 0

    changes = diff(old, new)
    print(f"check {now()}: revs {rev_summary(old, new)}, changes={len(changes)}")
    if changes:
        text = ("【4题质检结果变更告警】" + now() + "\n"
                + rev_summary(old, new) + "\n" + "\n".join(changes))
        print(text)
        if args.send:
            res = send_message(text, args.user)
            print(f"sent: message_id={res.get('data', {}).get('message_id')}")
    save_state(Path(args.state).resolve(), new)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
