---
name: autoresearch-loop
description: "在单个对话里跑「质检 → 修复 → 再质检」闭环：主会话只做总控，QA 子代理再派孙代理读分片，修复子代理改包；磁盘状态机 + 哈希闸门保证阶段可恢复、工作区不互相污染。适用于 AutoResearch/Harbor 题包与类似交付物。"
---

# autoresearch-loop

把质检和修复串成一个可恢复的循环，不用每轮开新对话搬运上下文。

## 三层角色

| 层 | 做什么 | 写哪里 |
|---|---|---|
| 主会话（总控） | 推进阶段、验哈希、抽检 2 条 blocking/major | 只读 `state.json` / `verdict.json` / `handoff.json` |
| QA 子代理（负责人） | 解包、建 worklist、派孙代理、合并、算覆盖率、写 verdict | `rounds/rNN/qa/` |
| 孙代理（分片） | 只读自己分片的文件，逐条答热点 | `rounds/rNN/qa/shards/<id>/` |
| 修复子代理 | 按 verdict 修包、跑 CPU 验收、打新包 | `rounds/rNN/repair/` |

## 目录隔离

```
loop/<task>/
  inputs/<包.zip>                  # 只读副本，永不在此解包
  rounds/r01/qa/{workspace,shards,gates,coverage,official}
  rounds/r01/repair/{src,out}
  state.json
```

每轮从 zip 重新解包到自己的目录；QA 与修复不共享工作树；阶段之间只传 zip + JSON，不传目录、不传聊天记录。

## 快速开始

```bash
python loopctl.py init --task auto2768 --zip "D:\...\包.zip" --max-rounds 3
python loopctl.py status
python loopctl.py next          # 打印当前阶段要派发的完整提示词
```

把 `next` 的输出原样交给子代理。子代理写完后：

```bash
# QA 轮
python loopctl.py record-qa --verdict "<qa>/verdict.json" --handoff "<qa>/handoff.json"

# 修复轮
python loopctl.py record-repair --zip "<新包.zip>" --report "<report.md>"

# 随时
python loopctl.py verify
python loopctl.py stop --reason "no_progress: <缺陷>"
```

## 状态机

```
qa --(blocking>0 且未到 max_rounds)--> repair --> qa ...
qa --(PASS/PASS_WITH_WAIVERS 且 blocking=0)--> done
qa --(round >= max_rounds)--> stopped
```

哈希闸门：`record-qa` 校验 `handoff.input_sha256` 等于当前输入；`record-repair` 拒绝与输入同哈希的“假修复包”；`verify` 随时复算输入 SHA。

## 文件

- `loopctl.py`：状态机 CLI（纯 stdlib）
- `prompts/00-main-orchestrator.md`：主会话总控纪律
- `prompts/01-qa-manager.md`：QA 负责人（含孙代理派发与合并）
- `prompts/02-shard-worker.md`：分片执行者模板
- `prompts/03-repair-agent.md`：修复代理
- `schemas/`：state / handoff / verdict 的 JSON Schema

## 设计边界

- 孙代理看不到主会话与子代理上下文，给它的提示词必须自带文件清单、输出目录与验收标准。
- 子代理要派孙代理，agent 类型必须是带 Agent 工具的（如 `general-purpose`）；只读 agent 派不了。
- 并发共享，建议每个 QA 子代理最多扇出 4 个孙代理，完成一个派下一个。
- 主控只读摘要 JSON，不读全量报告；抽检必须回源到 file:line。
