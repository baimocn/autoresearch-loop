# autoresearch-loop

在**一个对话**里跑「质检 → 修复 → 再质检」闭环。默认由 **workflows + loop** 驱动：workflow 脚本用 `world.run` 调 `loopctl.py` 推进磁盘状态，分片审阅由 workflow **顶层** agent 完成（每片一个独立上下文），不再有“子代理里再派孙代理”的嵌套。状态落盘、哈希守门、工作区按轮次和角色隔离。

**当前版本：v2.0.0（2026-11-25）**，对齐 `adversarial-engineering-qa` v2.7.0 与官方 `autoresearch-unified-qa v0.5.0-current.1`，并接入飞书群 2026-11-19～11-24 的返修指引与构建要求。完整变更见 [CHANGELOG.md](CHANGELOG.md)。

## 为什么需要它

前几轮质检的痛点很具体：每修一轮就要开新对话，把包、判级、修复清单重新搬一遍；子代理通道一断，整个上下文就废了。这套东西把状态从对话里挪到磁盘上：`state.json` 记到第几轮、当前该谁干活，模型切换或会话中断后，`status` 看一眼就能接着派。

## v2 的关键变化

| 项 | v1 | v2 |
|---|---|---|
| 结论 | `PASS / PASS_WITH_WAIVERS / FAIL / INCOMPLETE` | **`PASS / FAIL` 二态**；覆盖不足视为本轮未完成，不是结论 |
| 机械门禁 | 三个旧脚本各跑一遍 | `current_standard_gate.py` 单一入口 + 新增 `check_shipping_limits.py` |
| 体积/构建 | 无 | 包体/解压/单层上限、禁 Ubuntu 快照源、digest 固定、凭证扫描、架构检查 |
| 缺陷归类 | 只有 severity | 增加五类终检问题 `class` 与 `responsible`（专家/平台/双方/质检端） |
| 门禁与结论 | 互不约束 | `PASS` 必须两态门禁通过 **且** 体积检查无 fail，否则 `record-qa` 拒绝 |
| 标准来源 | 散落在提示词 | `docs/` 存飞书文档快照 + SHA-256，`state.standard` 记录本轮标准 |
| 调度 | 主会话派 QA 子代理，子代理再派孙代理（嵌套、无法复核） | **workflows + loop**：workflow 顶层 agent 做 QA 负责人/分片审阅/修复，全在同一层，无嵌套 |

## 角色与写权限

| 角色（workflow 顶层 agent） | 职责 | 只写 |
|---|---|---|
| workflow 脚本 | `world.run` 调 `loopctl.py` 推进状态、验哈希、收产物 | 只读状态与摘要 |
| QA 负责人 | 解包、建 worklist、跑门禁、合并分片、算覆盖率、写 verdict | `rounds/rNN/qa/` |
| 分片审阅员（每片一个） | 读自己那片文件，逐条答热点，写 findings/coverage | `rounds/rNN/qa/shards/<id>/` |
| 修复工程师 | 按 verdict 修包、跑 CPU 验收、自查体积、打新包 | `rounds/rNN/repair/` |

这些 agent 都由 workflow 脚本在**同一层**创建，彼此不嵌套。只有在无 workflow 宿主时才回退到手工串行模式（handoff 记 `serial_fallback`）。

## 推荐：workflows + loop（默认）

在 ZCode 里用 `/workflow` 运行 `workflows/qa_repair_loop.dwf.ts`，传入：

```
repoRoot   = 本仓库绝对路径
zip        = 输入题包 zip 绝对路径
loopRoot   = 循环根目录绝对路径
task       = 任务名，如 auto0823
skillDir   = adversarial-engineering-qa 绝对路径
maxRounds  = 3（默认）
shards     = 4（默认）
```

workflow 自动完成：`init` → 每轮 `verify/next/gate` → 顶层 agent 分片审阅 → `record-qa` → `record-repair` → 下一轮，直到 PASS 或到达上限。分片审阅是 workflow 顶层 agent，不嵌套。

## 手工/回退模式

```bash
# 0. 准备标准 skill（含 current_standard_gate.py）
git clone https://github.com/baimocn/adversarial-engineering-qa.git ../adversarial-engineering-qa
# 也可用 AUTORESEARCH_QA_SKILL 指定路径

# 1. 建循环（输入包会被复制到 inputs/，原包不动）
python loopctl.py init --task auto0823 --zip "/path/to/0823-完整提交包-v48.zip" --max-rounds 3

# 2. 看状态 / 拿当前阶段提示词
python loopctl.py status
python loopctl.py next

# 3. 解包后跑机械门禁
python loopctl.py gate --task-root "<解包树>/harbor_task"
# 产物：rounds/rNN/qa/gates/current-standard.json
#       rounds/rNN/qa/gates/shipping-limits.json

# 4. 产物落盘后推进状态机
python loopctl.py record-qa --verdict "<qa>/verdict.json" --handoff "<qa>/handoff.json"
python loopctl.py record-repair --zip "<新包.zip>" --report "<报告.md>"

# 随时
python loopctl.py verify
python loopctl.py stop --reason "no_progress: <缺陷>"
```

回退模式里没有 workflow 顶层 agent，QA 负责人自行按分片清单串行读，并在 `handoff.execution_mode` 记 `serial_fallback`。

## 目录布局

```
loop/<task>/
  inputs/<包.zip>                       # 只读副本，永不在此解包
  prompts/ schemas/ scripts/            # 自包含副本
  workflows/qa_repair_loop.dwf.ts       # 默认编排器（workflows + loop）
  rounds/r01/qa/
    workspace/extracted/                # 唯一解包树
    shards/0001..000N/                  # 孙代理输出，各自独立
    gates/                              # current-standard.json / shipping-limits.json
    verdict.json                        # QA 负责人合并后的结论（PASS/FAIL）
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
qa --(FAIL 且未到 max_rounds)--> repair --> qa ...
qa --(PASS 且 blocking=0)--> done
qa --(round >= max_rounds)--> stopped
qa --(同一组 blocking 连续两轮不变)--> stopped
```

哈希与完整性闸门：

1. `record-qa` 校验 `verdict.input_sha256` 与 `handoff.input_sha256` 都等于当前输入，`verdict.round` 等于当前轮次，防止拿别的包/别的轮次的结论冒充。
2. `record-qa` 拒绝：`decision` 不是 `PASS`/`FAIL`（含旧四档）；`PASS` 带 blocking 或 major；`FAIL` 没有 blocking；任一分片 `coverage.json` 缺失或 `complete=false`；`findings.json` 缺失；`mechanical_checks` 缺 `current_standard_gate` 或其文件不存在；`output_paths` 指向不存在的文件。
3. `record-qa` 的 PASS 硬约束：门禁产物二态结论为通过，且 `shipping_limits.ok != false`；缺任一项直接拒绝。
4. `record-qa` 做无进展检测：同一组 blocking（file:line:summary）连续两轮不变，直接转 `stopped`，不再空转。
5. `record-repair` 拒绝与输入同 SHA 的“假修复包”，并要求 `repair/handoff.json` 存在且 `input_sha256`/`output_sha256` 与实际一致。
6. `verify` 复算三样：当前输入、`inputs/` 里的原始包、历史每一轮修复包的哈希。任何一个被改过都会报 MISMATCH。

## 新增的机械检查：

`scripts/check_shipping_limits.py`（stdlib，纯静态）：

| 编号 | 检查 | 判定 |
|---|---|---|
| S-01 | 压缩后包体 ≤ 50 GiB | fail |
| S-02 | 解压总量 ≤ 50 GiB | fail |
| S-03 | 最大单文件 ≤ 50 GiB（单层 5 GiB 需平台实测） | fail / needs_manual |
| S-04 | 禁止 `snapshot.ubuntu.com` 时间戳快照 | fail |
| S-05 | `FROM` 固定 digest | needs_manual |
| S-06 | apt 安装固定版本 | needs_manual |
| S-07 | 构建非交互、不依赖登录态 | needs_manual |
| S-08 | 题包/Dockerfile 不含凭证 | fail |
| S-09 | 不用 `chmod 777` 兜底 | needs_manual |
| S-10 | 构建期不默认 GPU（`nvidia-smi`） | needs_manual |
| S-11 | 目标架构为 linux/amd64 | fail / needs_manual |
| S-12 | Compose 多容器需单独评审 | needs_manual |

`needs_manual` 不等于通过：要么给出证据放行，要么进 `platform_scope`。

## 对抗性自审（2026-11-08，v2 复检）

搭完后按“如果我是作弊的 agent，怎么绕过它”逐条攻击：

| 原缺口 | 绕过方式 | 现在的拦截 |
|---|---|---|
| verdict 自报计数 | 声明 `PASS`、`blocking_count=0`，但 `key_findings` 里藏着 blocking | `record-qa` 重算 key_findings 的 blocking，计数不一致直接拒绝 |
| 分片覆盖自报 | 只交 1 个分片、其余不写，仍报 PASS | 要求每个 shard 目录都有 `findings.json` + `coverage.json`；PASS 要求全部分片 100% |
| 机械门禁跳过 | 不跑门禁，verdict 里只写“已检查” | `mechanical_checks` 路径必须真实存在且含二态结论 |
| 门禁判 FAIL 仍写 PASS | 自己写个空 JSON 或忽略 rc | PASS 校验门禁结论；`decision=FAIL` 时 gate 必须真是 fail |
| 体积超标藏起来 | 不跑体积检查 | PASS 必须有 `shipping_limits` 产物且无 fail 项 |
| verdict 来源冒充 | handoff 哈希对，verdict 从别的包拷来 | verdict 与 handoff 的输入哈希、round 都校验 |
| 修复 handoff 造假 | 交一个新 zip，但 handoff 的 input/output 哈希对不上 | `record-repair` 校验 handoff 存在且两个哈希一致 |
| 原始包被改 | 只改 `inputs/` 里的副本，当前输入看起来正常 | `verify` 同时校验 `inputs/` 副本与 `original_sha256` |
| 历史修复包被替换 | 回填一个旧包冒充本轮产物 | `verify` 逐轮校验历史修复包哈希 |
| 无限循环 | 每轮都报同一个 blocking，永远修不好 | 同一组 blocking 连续两轮不变 → 自动 stopped |
| zip 路径穿越 | 包里放 `../../x` 或符号链接，写到工作区外 | QA 提示词强制过滤绝对路径/`..`/symlink，并设文件数与体积上限 |
| 平台故障冒充专家缺陷 | 把平台侧问题记成 blocking，挡掉 PASS | `responsible` 字段；平台侧项不计入 blocking，需单独说明 |
| 旧标准复活 | 继续用 PASS_WITH_WAIVERS 绕过 major | 旧四档结论被 `record-qa` 直接拒绝 |

仍然存在的结构性限制（不装作能解决）：

- **finding 真假**：状态机只能保证格式与哈希，判断一条 finding 是否成立仍靠主控抽检回源。
- **越界读文件**：目录隔离靠提示词与约定，不是文件系统权限；真正硬隔离要靠容器或只读挂载。
- **嵌套派发**：孙代理是否真的被派过、是否降级为串行，只能靠 `execution_mode` 自报，无法机器复核。
- **GPU 挂账**：`platform_scope` 是否真的需要 GPU，只能靠抽检证据，不能自动判定。
- **镜像体积/层数**：需要实际构建才能测，静态只查包体与最大单文件。

## 工作区隔离（核心）

- `inputs/` 只读：`init` 复制一份，之后任何阶段都不碰原包。
- 每轮独立解包：第 2 轮的 QA 解的是第 1 轮修复产出的新 zip，不是第 1 轮的 workspace。
- QA 与修复不共享目录：修复代理读 verdict，但不读 `qa/workspace`；QA 代理不写 `repair/`。
- 孙代理只写 `shards/<id>/`：每个分片一个输出根，互不覆盖。
- 阶段之间只传 zip 与 JSON：不传目录、不传聊天记录。

## 提示词、schema 与文档

- `prompts/00-main-orchestrator.md`：主会话总控纪律（抽检、停止条件、失败恢复）。
- `prompts/01-qa-manager.md`：QA 负责人（门禁、五类问题、交付合同、coverage、verdict/handoff 模板）。
- `prompts/02-shard-worker.md`：分片执行者模板（含 `class`/`responsible` 与五个必查位置）。
- `prompts/03-repair-agent.md`：修复代理（按类修法、交付底线、CPU 验收、platform_scope）。
- `schemas/`：`state` / `handoff` / `verdict` 的 JSON Schema（v2）。
- `docs/`：飞书文档快照与来源索引 `00-sources.md`。

`loopctl.py next` 会把当前阶段的模板填好路径直接打印，交给子代理时不要改写。

## 自测

```bash
python selftest.py
```

在临时目录跑完 `qa → repair → qa → done`，并验证拒绝路径（哈希不符、覆盖不足、旧结论、PASS 无门禁、假修复包、原始包被篡改）。不碰真实题包。

## 已知边界

- 孙代理看不到主会话与子代理上下文，提示词必须自带文件清单、输出目录、验收标准。
- QA 子代理要派孙代理，agent 类型必须是带 Agent 工具的（如 `general-purpose`）；只读 agent 派不了。
- 并发共享，建议每个 QA 子代理最多扇出 4 个孙代理，完成一个派下一个；通道失败时降级为串行，并在 `handoff.execution_mode` 记 `serial_fallback`。
- 主控只读 `state.json` / `verdict.json` / `handoff.json`；全量报告留在子代理的输出目录，需要时按路径抽查，不要整份塞进上下文。
- GPU/实机/Docker 项在 verdict 的 `platform_scope` 里列，不判 FAIL，也不写成 pass。
- 政策文档（折价验收、GPU 报销）只影响优先级与结算口径，不改变技术判定。
