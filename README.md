# autoresearch-loop

在**一个对话**里跑「质检 → 修复 → 再质检」闭环的本地工具。主会话只做总控，QA 子代理再派孙代理读分片，修复子代理改包；状态落盘、哈希守门、工作区按轮次和角色隔离。

> 这是本地工具，**不推 GitHub**。要分享时再单独建私有仓库。

## 为什么需要它

前几轮质检的痛点很具体：每修一轮就要开新对话，把包、判级、修复清单重新搬一遍；子代理通道一断，整个上下文就废了。这套东西把状态从对话里挪到磁盘上：`state.json` 记到第几轮、当前该谁干活，模型切换或会话中断后，`status` 看一眼就能接着派。

## 三层角色

| 层 | 职责 | 只写 |
|---|---|---|
| 主会话（总控） | `loopctl.py next` 拿提示词、派发、验哈希、抽检 2 条 blocking/major | 不写业务文件，只读状态与摘要 |
| QA 子代理（负责人） | 解包、建 worklist、派孙代理、合并、算覆盖率、写 verdict | `rounds/rNN/qa/` |
| 孙代理（分片） | 读自己那片文件，逐条答热点，写 findings/coverage | `rounds/rNN/qa/shards/<id>/` |
| 修复子代理 | 按 verdict 修包、跑 CPU 验收、打新包 | `rounds/rNN/repair/` |

## 快速开始

```bash
cd D:\Desktop\论文最新包\autoresearch-loop

# 1. 建循环（输入包会被复制到 inputs/，原包不动）
python loopctl.py init --task auto0823 --zip "D:\Desktop\论文最新包\0823\完整提交包_auto0823_v44.zip" --max-rounds 3

# 2. 看状态
python loopctl.py status

# 3. 拿当前阶段的完整提示词，原样交给子代理
python loopctl.py next
```

子代理干完活、产物落盘后：

```bash
# QA 轮
python loopctl.py record-qa --verdict "<qa>/verdict.json" --handoff "<qa>/handoff.json"

# 修复轮
python loopctl.py record-repair --zip "<新包.zip>" --report "<报告.md>"

# 随时
python loopctl.py verify
python loopctl.py stop --reason "no_progress: <缺陷>"
```

## 目录布局

```
loop/<task>/
  inputs/<包.zip>                       # 只读副本，永不在此解包
  rounds/r01/qa/
    workspace/                          # 唯一解包树（prepare_review_workspace 或 zipfile）
    shards/0001..000N/                  # 孙代理输出，各自独立
    gates/ coverage/ official/
    verdict.json                        # QA 负责人合并后的结论
    handoff.json                        # 交接单（输入哈希、分片覆盖、命令 rc）
  rounds/r01/repair/
    src/                                # 从 zip 重新解包，绝不复用 qa/workspace
    out/<新包>.zip
    report.md
  state.json
  claims/
```

## 状态机

```
qa --(blocking>0，未到 max_rounds)--> repair --> qa ...
qa --(PASS/PASS_WITH_WAIVERS 且 blocking=0)--> done
qa --(round >= max_rounds)--> stopped
```

三道哈希闸门：

1. `record-qa` 校验 `verdict.input_sha256` 与 `handoff.input_sha256` 都等于当前输入，`verdict.round` 等于当前轮次，防止拿别的包/别的轮次的结论冒充。
2. `record-qa` 拒绝这些情况：`PASS*` 带 blocking、`PASS` 带 major、任一分片 `coverage.json` 缺失或 `complete=false`、`findings.json` 缺失、`mechanical_checks` 三件产物缺失、`output_paths` 指向不存在的文件。
3. `record-qa` 做无进展检测：同一组 blocking（file:line:summary）连续两轮不变，直接转 `stopped`，不再空转。
4. `record-repair` 拒绝与输入同 SHA 的“假修复包”，并要求 `repair/handoff.json` 存在且 `input_sha256`/`output_sha256` 与实际一致。
5. `verify` 复算三样：当前输入、`inputs/` 里的原始包、历史每一轮修复包的哈希。任何一个被改过都会报 MISMATCH。

## 对抗性自审（2026-11-08）

搭完后按“如果我是作弊的 agent，怎么绕过它”逐条攻击，补了 9 个口子：

| 原缺口 | 绕过方式 | 现在的拦截 |
|---|---|---|
| verdict 自报计数 | 声明 `PASS`、`blocking_count=0`，但 `key_findings` 里藏着 blocking | `record-qa` 重算 key_findings 的 blocking，计数不一致直接拒绝 |
| 分片覆盖自报 | 只交 1 个分片、其余不写，仍报 PASS | 要求每个 shard 目录都有 `findings.json` + `coverage.json`；PASS 要求全部分片 100% |
| 机械门禁跳过 | 不跑三个检查脚本，verdict 里只写“已检查” | `mechanical_checks` 三个路径必须真实存在 |
| verdict 来源冒充 | handoff 哈希对，verdict 从别的包拷来 | verdict 与 handoff 的输入哈希、round 都校验 |
| 修复 handoff 造假 | 交一个新 zip，但 handoff 的 input/output 哈希对不上 | `record-repair` 校验 handoff 存在且两个哈希一致 |
| 原始包被改 | 只改 `inputs/` 里的副本，当前输入看起来正常 | `verify` 同时校验 `inputs/` 副本与 `original_sha256` |
| 历史修复包被替换 | 回填一个旧包冒充本轮产物 | `verify` 逐轮校验历史修复包哈希 |
| 无限循环 | 每轮都报同一个 blocking，永远修不好 | 同一组 blocking 连续两轮不变 → 自动 stopped |
| zip 路径穿越 | 包里放 `../../x` 或符号链接，写到工作区外 | QA 提示词强制过滤绝对路径/`..`/symlink，并设文件数与体积上限 |

仍然存在的结构性限制（不装作能解决）：

- **finding 真假**：状态机只能保证格式与哈希，判断一条 finding 是否成立仍靠主控抽检回源。
- **越界读文件**：目录隔离靠提示词与约定，不是文件系统权限；真正硬隔离要靠容器或只读挂载。
- **嵌套派发**：孙代理是否真的被派过、是否降级为串行，只能靠 `execution_mode` 自报，无法机器复核。
- **GPU 挂账**：`platform_scope` 是否真的需要 GPU，只能靠抽检证据，不能自动判定。

## 工作区隔离（核心）

- `inputs/` 只读：`init` 复制一份，之后任何阶段都不碰原包。
- 每轮独立解包：第 2 轮的 QA 解的是第 1 轮修复产出的新 zip，不是第 1 轮的 workspace。
- QA 与修复不共享目录：修复代理读 verdict，但不读 `qa/workspace`；QA 代理不写 `repair/`。
- 孙代理只写 `shards/<id>/`：每个分片一个输出根，互不覆盖。
- 阶段之间只传 zip 与 JSON：不传目录、不传聊天记录。

## 提示词与 schema

- `prompts/00-main-orchestrator.md`：主会话总控纪律（抽检、停止条件、失败恢复）。
- `prompts/01-qa-manager.md`：QA 负责人（含孙代理派发、coverage、verdict/handoff 模板）。
- `prompts/02-shard-worker.md`：分片执行者模板。
- `prompts/03-repair-agent.md`：修复代理（根因、相邻路径、CPU 验收、platform_scope）。
- `schemas/`：`state` / `handoff` / `verdict` 的 JSON Schema。

`loopctl.py next` 会把当前阶段的模板填好路径直接打印，交给子代理时不要改写。

## 自测

```bash
python selftest.py
```

在临时目录跑完 `qa → repair → qa → done`，并验证两条哈希拒绝路径。不碰真实题包。

## 已知边界

- 孙代理看不到主会话与子代理上下文，提示词必须自带文件清单、输出目录、验收标准。
- QA 子代理要派孙代理，agent 类型必须是带 Agent 工具的（如 `general-purpose`）；只读 agent 派不了。
- 并发共享，建议每个 QA 子代理最多扇出 4 个孙代理，完成一个派下一个；通道失败时降级为串行，并在 `handoff.execution_mode` 记 `serial_fallback`。
- 主控只读 `state.json` / `verdict.json` / `handoff.json`；全量报告留在子代理的输出目录，需要时按路径抽查，不要整份塞进上下文。
- GPU/实机/Docker 项在 verdict 的 `platform_scope` 里列，不判 FAIL，也不写成 pass。
