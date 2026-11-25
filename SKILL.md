---
name: autoresearch-loop
description: "在单个对话里跑「质检 → 修复 → 再质检」闭环：主会话只做总控，QA 子代理再派孙代理读分片，修复子代理改包；磁盘状态机 + 两条哈希闸门 + 二态机械门禁保证阶段可恢复、结论可复核、工作区不互相污染。适用于 AutoResearch/Harbor 题包与类似交付物。"
---

# autoresearch-loop

把质检和修复串成一个可恢复的循环，不用每轮开新对话搬运上下文。结论二态：**PASS / FAIL**。

**调度方式：workflows + loop（默认）**。用 `workflows/qa_repair_loop.dwf.ts` 作为唯一编排器：workflow 脚本用 `world.run` 调 `loopctl.py` 推进磁盘状态机，分片审阅由 workflow **顶层** `agent()` 完成（每个分片一个独立上下文），**不再使用“子代理里再派孙代理”的嵌套派发**。只有在没有 workflow 宿主时才回退到手工模式：主会话按 `loopctl.py next` 派一个 `general-purpose` 子代理，由它自己串行读分片并记 `serial_fallback`。

## 角色与写权限

| 角色（workflow 顶层 agent） | 做什么 | 写哪里 |
|---|---|---|
| workflow 脚本（编排） | `world.run` 调 `loopctl.py`：init/verify/next/gate/record-qa/record-repair，推进状态 | 只读状态与摘要，不写业务文件 |
| QA 负责人 agent | 解包、跑门禁、写 worklist 分片清单、合并分片、算覆盖率、写 verdict/handoff | `rounds/rNN/qa/` |
| 分片审阅员 agent（每片一个，顶层） | 只读自己分片的文件，逐条答热点 | `rounds/rNN/qa/shards/<id>/` |
| 修复工程师 agent | 按 verdict 修包、跑 CPU 验收、自查体积、打新包 | `rounds/rNN/repair/` |

这些 agent 都由 workflow 脚本在**同一层**创建，彼此不嵌套、不互相调用 Agent 工具。

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
python loopctl.py init --task auto2768 --zip "/path/to/包.zip" --max-rounds 3
python loopctl.py status
python loopctl.py next          # 打印当前阶段要派发的完整提示词
python loopctl.py gate --task-root "<解包树>/harbor_task"   # 两态门禁 + 体积检查
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
qa --(FAIL 且未到 max_rounds)--> repair --> qa ...
qa --(PASS 且 blocking=0)--> done
qa --(round >= max_rounds)--> stopped
qa --(同一组 blocking 连续两轮不变)--> stopped
```

哈希与门禁：`record-qa` 校验 verdict/handoff 的 `input_sha256` 与 round，并要求 `current_standard_gate` 产物存在；`PASS` 还要求门禁判定通过、`shipping_limits` 无 fail。`record-repair` 拒绝与输入同哈希的“假修复包”；`verify` 随时复算输入、原始包与历轮修复包 SHA。

## 检查口径

- 机械：`adversarial-engineering-qa/scripts/current_standard_gate.py`（v2.7 唯一入口，二态）+ 本仓库 `scripts/check_shipping_limits.py`（体积、快照源、digest、apt 版本、凭证、chmod 777、构建期 GPU、amd64、Compose）。
- 五类终检问题：难度过低、难度过高/无高分合法解（`[0.15, 0.8]`）、失败写成普通 0、Verifier 预算失配、评分门/合同与防作弊。
- 交付合同：`solution/solve.sh` Oracle 入口、依赖锁全、正式 reward 二选一、候选 hard gate −10。
- 平台侧故障与专家侧缺陷分开记（`responsible`），平台项不计 blocking。

## 文件

- `loopctl.py`：状态机 CLI（纯 stdlib）
- `scripts/check_shipping_limits.py`：交付体积与构建可复现性检查（纯 stdlib）
- `workflows/qa_repair_loop.dwf.ts`：**默认编排器**（workflows + loop，顶层 agent，不嵌套）
- `prompts/00-main-orchestrator.md`：总控纪律（默认 workflow 模式 + 手工回退）
- `prompts/01-qa-manager.md`：QA 负责人（门禁、五类问题、合并）
- `prompts/02-shard-worker.md`：分片审阅员模板
- `prompts/03-repair-agent.md`：修复工程师
- `schemas/`：state / handoff / verdict 的 JSON Schema（v2）
- `docs/`：飞书文档快照与 `00-sources.md` 来源索引

## 设计边界

- 孙代理看不到主会话与子代理上下文，给它的提示词必须自带文件清单、输出目录与验收标准。
- 嵌套派发已被移除：分片审阅由 workflow 顶层 agent 完成，审计轨迹直接来自 workflow，不再靠 `execution_mode` 自报。
- 并发共享，workflow 中同轮的分片审阅员并行；建议每轮不超过 4 个，完成一个由下一轮接续。
- 主控只读摘要 JSON，不读全量报告；抽检必须回源到 file:line。
- 镜像体积与实际层数要构建后才知道，静态检查只覆盖包体与最大单文件。
