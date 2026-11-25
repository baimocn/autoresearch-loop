#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_shipping_limits.py —— 交付体积与构建可复现性检查（stdlib）。

来源：飞书「返修指引」「ICM 构建环境与 Dockerfile 兼容性说明」（2026-11-19/24）。
只做静态判定：体积、快照源、基础镜像固定、非交互安装、凭证、权限、GPU 构建默认值。

用法：
    python check_shipping_limits.py --input <包.zip> --root <解包树> --out <out.json>
                                    [--max-package-gib 50] [--max-uncompressed-gib 50]

口径（能机械判的判 fail，需人判的记 needs_manual，绝不把未知写成 pass）：
  S-01 包体（压缩后）≤ 50 GiB                     fail
  S-02 解压后总量 ≤ 50 GiB                        fail
  S-03 单个文件 ≤ 50 GiB（近似单层 Layer 上限预警） needs_manual / fail > 50 GiB
  S-04 禁止 Ubuntu 时间戳快照源                    fail
  S-05 基础镜像应固定 digest                       needs_manual
  S-06 apt 安装应固定版本                          needs_manual
  S-07 构建不得交互/不得登录                       needs_manual
  S-08 题包与 Dockerfile 不得含凭证                fail
  S-09 不得 chmod 777 兜底                        needs_manual
  S-10 构建期默认无 GPU，nvidia-smi 需申报          needs_manual
  S-11 架构应支持 linux/amd64                      fail
  S-12 Compose 多容器需单独评审                    needs_manual
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

GIB = 1 << 30
MAX_PACKAGE_GIB = 50
MAX_UNCOMPRESSED_GIB = 50
SINGLE_FILE_GIB = 50

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]
SKIP_SECRET_SUFFIX = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".whl", ".pt", ".bin", ".safetensors", ".parquet", ".npy", ".npz"}
MAX_SECRET_SCAN_BYTES = 8 * 1024 * 1024


def add(checks: list[dict], cid: str, status: str, detail: str,
        file: str | None = None, line: int | None = None) -> None:
    row = {"id": cid, "status": status, "detail": detail}
    if file:
        row["file"] = file
    if line:
        row["line"] = line
    checks.append(row)


def zip_stats(path: Path) -> dict:
    """压缩后体积、解压总量、文件数、最大单文件。包损坏时抛 BadZipFile。"""
    total_unc = 0
    largest = ("", 0)
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise zipfile.BadZipFile(f"条目损坏：{bad}")
        infos = z.infolist()
        for i in infos:
            total_unc += i.file_size
            if i.file_size > largest[1]:
                largest = (i.filename, i.file_size)
    return {
        "package_bytes": path.stat().st_size,
        "uncompressed_bytes": total_unc,
        "entries": len(infos),
        "largest_name": largest[0],
        "largest_bytes": largest[1],
    }


def dockerfiles(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
            out.append(p)
    return sorted(out)


def scan_no_snapshot(path: Path, root: Path, checks: list[dict]) -> None:
    rel = str(path.relative_to(root))
    for no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        low = raw.lower()
        if "snapshot.ubuntu.com" in low:
            add(checks, "S-04", "fail", "使用了 Ubuntu 时间戳快照源（平台禁止）；改用离线依赖或发行版固定版本",
                rel, no)
        if re.search(r"\bapt(-get)?\s+install\b", low) and "=" not in low and "install -y" in low:
            add(checks, "S-06", "needs_manual", "apt 安装未固定版本；需提供版本锁定与离线闭包", rel, no)
        if "chmod 777" in low or "chmod -r 777" in low:
            add(checks, "S-09", "needs_manual", "使用 chmod 777 兜底；应按文件、用户与读写需求修复权限", rel, no)
        if re.search(r"\b(curl|wget)\b[^\n]*\|\s*(bash|sh)\b", low):
            add(checks, "S-07", "needs_manual", "下载后直接管道执行，非交互与可复现性存疑；改为固定哈希的离线制品", rel, no)
        if re.search(r"\b(ssh|git\s+clone\s+git@)", low):
            add(checks, "S-07", "needs_manual", "构建期依赖外部登录态或 SSH；需改成公开源或离线交付", rel, no)
        if "nvidia-smi" in low:
            add(checks, "S-10", "needs_manual", "构建期调用 nvidia-smi；需申报构建机 GPU 条件（装 CUDA 工具链本身不算）", rel, no)
        if "--platform" in low and "linux/amd64" not in low and "x86_64" not in low:
            add(checks, "S-11", "fail", "显式声明了非 linux/amd64 平台；目标架构为 x86_64", rel, no)
        m = re.search(r"(?i)^\s*from\s+(\S+)", raw)
        if m:
            ref = m.group(1)
            if "@sha256:" not in ref:
                add(checks, "S-05", "needs_manual", f"基础镜像未固定 digest：{ref}", rel, no)
            if any(t in ref.lower() for t in ("arm64", "aarch64")):
                add(checks, "S-11", "needs_manual", f"基础镜像可能只支持 ARM：{ref}", rel, no)
        if re.search(r"(?i)^\s*(arg|env)\s+\S*(token|secret|password|api_key|access_key)", raw):
            add(checks, "S-08", "fail", "Dockerfile 中声明了凭证类 ARG/ENV；不得把凭证写入交付内容", rel, no)


def scan_compose(root: Path, checks: list[dict]) -> None:
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        for p in root.rglob(name):
            add(checks, "S-12", "needs_manual", "存在 Compose 编排；平台 Compose 适配需单独评审，不得删服务强行适配",
                str(p.relative_to(root)))


def scan_secrets(root: Path, checks: list[dict]) -> None:
    """每个文件最多报一条，避免同一份配置刷出上百条。"""
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() in SKIP_SECRET_SUFFIX:
            continue
        try:
            if p.stat().st_size > MAX_SECRET_SCAN_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        hit = None
        for no, raw in enumerate(text.splitlines(), 1):
            if len(raw) > 4096:
                continue
            if any(pat.search(raw) for pat in SECRET_PATTERNS):
                hit = no
                break
        if hit is not None:
            add(checks, "S-08", "fail", "疑似凭证泄漏（值已脱敏）", str(p.relative_to(root)), hit)


def main() -> int:
    ap = argparse.ArgumentParser(description="交付体积与构建可复现性检查")
    ap.add_argument("--input", required=True, help="提交包 zip")
    ap.add_argument("--root", required=True, help="解包后的树根（含 Dockerfile）")
    ap.add_argument("--out", required=True, help="输出 JSON")
    ap.add_argument("--max-package-gib", type=float, default=MAX_PACKAGE_GIB)
    ap.add_argument("--max-uncompressed-gib", type=float, default=MAX_UNCOMPRESSED_GIB)
    args = ap.parse_args()

    zpath = Path(args.input).resolve()
    root = Path(args.root).resolve()
    if not zpath.is_file():
        print(f"ERROR: 包不存在：{zpath}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"ERROR: 解包树不存在：{root}", file=sys.stderr)
        return 2

    checks: list[dict] = []
    try:
        stats = zip_stats(zpath)
    except (OSError, zipfile.BadZipFile) as exc:
        add(checks, "S-00", "fail", f"包无法读取或已损坏：{exc}")
        stats = {}
    if stats:
        pkg_gib = stats["package_bytes"] / GIB
        unc_gib = stats["uncompressed_bytes"] / GIB
        add(checks, "S-01",
            "pass" if pkg_gib <= args.max_package_gib else "fail",
            f"压缩后 {pkg_gib:.2f} GiB / 上限 {args.max_package_gib:g} GiB")
        add(checks, "S-02",
            "pass" if unc_gib <= args.max_uncompressed_gib else "fail",
            f"解压后 {unc_gib:.2f} GiB / 上限 {args.max_uncompressed_gib:g} GiB")
        largest_gib = stats["largest_bytes"] / GIB
        add(checks, "S-03",
            "pass" if largest_gib <= SINGLE_FILE_GIB else "fail",
            f"最大单文件 {largest_gib:.2f} GiB（{stats['largest_name']}）；单层 Layer 上限 5 GiB 需平台实测",
            stats["largest_name"] or None)

    dfs = dockerfiles(root)
    if not dfs:
        add(checks, "S-04", "needs_manual", "未找到 Dockerfile；无法核对快照源与构建约定")
    for df in dfs:
        scan_no_snapshot(df, root, checks)
    scan_compose(root, checks)
    scan_secrets(root, checks)

    hard_fail = [c for c in checks if c["status"] == "fail"]
    manual = [c for c in checks if c["status"] == "needs_manual"]
    result = {
        "schema": "shipping-limits/1",
        "source": [
            "feishu:返修指引(VoUAwGA8Eil5Tgk70Q4cV9HJnjb)",
            "feishu:ICM构建环境与Dockerfile兼容性(DrcBwlqM8iucMrkHIwtctj4TnIh)",
        ],
        "ok": not hard_fail,
        "summary": {
            "fail": len(hard_fail),
            "needs_manual": len(manual),
            "pass": len([c for c in checks if c["status"] == "pass"]),
        },
        "stats": stats,
        "checks": checks,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"fail={len(hard_fail)} needs_manual={len(manual)} ok={result['ok']}")
    for c in hard_fail:
        print(f"  FAIL {c['id']} {c['detail']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
