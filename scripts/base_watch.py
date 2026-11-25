#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""base_watch.py —— 盯住飞书多维表格，变更时通过 bot 私聊推送告警。

背景：平台改「结算公示表」后，专家需要在飞书里及时知道。
飞书 CLI 的事件系统（lark-cli event list）没有 Base 记录变更事件，
所以用**轮询 + manifest.rev 比对**：rev 变了才拉全表比对字段差异。

读表用用户身份（有 base 权限），发消息用 bot 身份（有 im:message 权限），
两者都是本机 lark-cli，不存任何 token。

用法：
    # 1. 建立基线（第一次跑，不告警）
    python scripts/base_watch.py --state /path/state.json snapshot

    # 2. 之后每次轮询：只告警变化
    python scripts/base_watch.py --state /path/state.json check --send

    # 3. 看当前差异但不发送
    python scripts/base_watch.py --state /path/state.json check

    # 4. 配合 cron：每 5 分钟
    */5 * * * * cd <repo> && python3 scripts/base_watch.py --state .base_watch.json check --send >> .base_watch.log 2>&1

依赖：lark-cli 在 PATH 上；用户身份已授权 base 读取，bot 可发私聊。
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

# 结算公示表（来自飞书群 2026-11-24 公告的政策文档）
DEFAULT_BASE = "UuOqbMBxCae9fdsu7e2cBjggnOf"
DEFAULT_TABLE = "tbltCUxO9l58WLTY"
DEFAULT_VIEW = "vewaBHqjsn"
# 告警发给谁（用户 open_id，来自 lark-cli auth status）
DEFAULT_USER = "ou_f07842e37250cc424b9701edc6811707"

# 我们关心的字段：这些变了才告警；其他字段（如备注、申诉说明）变化不刷屏。
WATCH_FIELDS = (
    "题目ID", "当前题目状态", "最优质检结果", "验收政策",
    "结算批次", "金额确认", "题目结算价格", "结算总价格（不含高产激励）",
    "是否选择结算", "一次成功", "早鸟激励", "统一追加返修激励",
    "专家申诉说明", "主办方备注",
)
FIELD_LABEL = {
    "当前题目状态": "状态", "最优质检结果": "质检", "验收政策": "验收",
    "结算批次": "批次", "金额确认": "金额确认", "题目结算价格": "结算价",
    "结算总价格（不含高产激励）": "总价", "是否选择结算": "选择结算",
    "一次成功": "一次成功", "早鸟激励": "早鸟",
    "统一追加返修激励": "返修激励", "专家申诉说明": "申诉说明", "主办方备注": "平台备注",
}


def lark(*args: str, as_user: str = "user", expect_envelope: bool = True) -> dict:
    """跑 lark-cli 并解析 JSON；失败抛异常，不吞错。

    expect_envelope=False 用于 +record-list --format ndjson：它直接打印 manifest，
    没有 ok 信封，成功与否只看 rc。
    """
    cmd = ["lark-cli", *args, "--as", as_user, "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"lark-cli 输出无法解析: {exc}\n{proc.stdout[:500]}\n{proc.stderr[:500]}") from exc
    if proc.returncode != 0:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise RuntimeError(f"lark-cli 调用失败 (rc={proc.returncode}): "
                           f"{err.get('message', proc.stderr[:300])}")
    if expect_envelope and data.get("ok") is not True:
        err = data.get("error", {})
        raise RuntimeError(f"lark-cli 业务失败: {err.get('message', str(data)[:300])}")
    return data


def fetch_snapshot(base: str, table: str, view: str | None) -> dict:
    """拉表并读 manifest.rev + 记录，返回可比较的快照。"""
    with tempfile.TemporaryDirectory(prefix="base_watch_") as td:
        out = str(Path(td) / "records.ndjson")
        args = ["base", "+record-list", "--base-token", base,
                "--table-id", table, "--format", "ndjson",
                "--output", out, "--overwrite"]
        if view:
            args += ["--view-id", view]
        manifest = lark(*args, expect_envelope=False)
        # manifest 由 stdout 返回，同时落盘为 records.manifest.json
        rows = [json.loads(line) for line in Path(out).read_text(encoding="utf-8").splitlines() if line.strip()]

    by_task: dict[str, dict] = {}
    for row in rows:
        task = str(row.get("题目ID") or row.get("record_id") or "?")
        by_task[task] = {
            key: normalize(row.get(key))
            for key in WATCH_FIELDS if key in row
        }
    return {"rev": manifest.get("rev"), "rows": by_task, "fetched_at": now()}


def normalize(value) -> str:
    """把列表/数字/None 归一成可比较、可读的字符串。"""
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        return "/".join(str(x) for x in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def diff(old: dict, new: dict) -> list[str]:
    """返回人类可读的变更行；只比 WATCH_FIELDS。"""
    lines: list[str] = []
    old_rows = old.get("rows", {})
    new_rows = new.get("rows", {})
    for task in sorted(set(old_rows) | set(new_rows)):
        before = old_rows.get(task)
        after = new_rows.get(task)
        if before is None:
            lines.append(f"🆕 **{task}** 新增记录")
            continue
        if after is None:
            lines.append(f"❌ **{task}** 记录被删除")
            continue
        changes = []
        for key in WATCH_FIELDS:
            b = before.get(key, "")
            a = after.get(key, "")
            if b != a:
                label = FIELD_LABEL.get(key, key)
                changes.append(f"  - {label}: `{b or '空'}` → `{a or '空'}`")
        if changes:
            lines.append(f"🔄 **{task}**")
            lines.extend(changes)
    return lines


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
    ap = argparse.ArgumentParser(description="飞书表格变更 → bot 私聊告警")
    ap.add_argument("action", choices=("snapshot", "check"),
                    help="snapshot=建基线不告警；check=比对并（可选）告警")
    ap.add_argument("--state", required=True, help="状态文件路径（存上次快照）")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--table", default=DEFAULT_TABLE)
    ap.add_argument("--view", default=DEFAULT_VIEW, help="留空 '' 表示读全部记录")
    ap.add_argument("--user", default=DEFAULT_USER, help="告警接收人 open_id")
    ap.add_argument("--send", action="store_true", help="check 时真的发消息（默认只打印）")
    args = ap.parse_args()

    state_path = Path(args.state).resolve()
    new = fetch_snapshot(args.base, args.table, args.view or None)
    old = load_state(state_path)

    if args.action == "snapshot":
        save_state(state_path, new)
        print(f"baseline saved: rev={new['rev']} tasks={len(new['rows'])} -> {state_path}")
        return 0

    if old is None:
        save_state(state_path, new)
        print(f"no baseline found; created one: rev={new['rev']}")
        return 0

    changes = diff(old, new)
    rev_changed = old.get("rev") != new.get("rev")
    print(f"check {now()}: rev {old.get('rev')} -> {new.get('rev')} "
          f"(changed={rev_changed}), field changes={len(changes)}")

    if changes:
        header = f"【结算表变更告警】{now()}\n修订号 rev {old.get('rev')} → {new.get('rev')}\n"
        text = header + "\n".join(changes)
        print(text)
        if args.send:
            result = send_message(text, args.user)
            print(f"sent: message_id={result.get('data', {}).get('message_id')}")
    elif rev_changed:
        print("rev 变化但关注字段无差异（可能是视图/排序调整），不告警。")

    save_state(state_path, new)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
