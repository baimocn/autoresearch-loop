# 变更记录

> 当前版本：**v2.1.0**（workflows + loop，无嵌套子代理）。

## v2.1.0 — 2026-11-25

**调度改为 workflows + loop，移除嵌套子代理。**

- 新增 `workflows/qa_repair_loop.dwf.ts` 作为默认编排器：workflow 脚本用
  `world.run` 调 `loopctl.py` 推进状态机（init/verify/next/gate/record-qa/
  record-repair），分片审阅由 workflow **顶层** `agent()` 完成，每片一个
  独立上下文，彼此不嵌套、不调用 Agent 工具。
- `prompts/00`、`prompts/01`、`SKILL.md`、`README.md` 全部改为 workflow 优先：
  “QA 子代理再派孙代理”改为“顶层 agent + 分片审阅员”；保留无 workflow
  宿主时的手工串行回退（`serial_fallback`）。
- `selftest.py` 扩充到 11 条拒绝路径，覆盖二态结论、门禁与体积约束。

## v2.0.0 — 2026-11-25

对齐 `adversarial-engineering-qa` v2.7.0（current-standard）与官方
`autoresearch-unified-qa v0.5.0-current.1`，并接入飞书群「A5-autosearch作业群」
2026-11-19～11-24 的新文档。

### 结论二态

- `decision` 只接受 `PASS` / `FAIL`。`PASS_WITH_WAIVERS`、`PASS_WITH_OPEN`、
  `INCOMPLETE` 从 schema 与 `loopctl.py` 中移除；写入旧值会被 `record-qa` 直接拒绝。
- 覆盖不足不再产出结论：补齐后重交，禁止把分片未完成写成任何一种通过。

### 机械门禁收敛

- 唯一机械入口改为 `current_standard_gate.py`（v2.7）。旧三件套
  （`platform_rules` / `delivery_contract` / `execution_path`）降为兼容路径。
- `PASS` 现在需要三件证据同时成立：门禁二态结论为通过、`shipping_limits` 无
  `fail`、全部分片 100% 覆盖。缺任一项 `record-qa` 拒绝。
- 新增 `loopctl.py gate` 子命令，一次跑完两态门禁与体积检查并写入
  `rounds/rNN/qa/gates/`。

### 新增检查：交付体积与构建可复现性

`scripts/check_shipping_limits.py`（纯 stdlib，静态）：

- S-01/S-02 包体 ≤ 50 GiB、解压总量 ≤ 50 GiB；S-03 最大单文件预警单层 5 GiB。
- S-04 禁止 `snapshot.ubuntu.com` 时间戳快照源。
- S-05 `FROM` 固定 digest；S-06 apt 固定版本；S-07 构建非交互/不依赖登录态。
- S-08 凭证泄漏（Dockerfile ARG/ENV 与包内扫描）。
- S-09 `chmod 777`；S-10 构建期 `nvidia-smi`；S-11 架构非 amd64；
  S-12 Compose 多容器。

### 缺陷归并与责任

- `key_findings` 增加 `class`（五类终检问题 + 交付/Oracle/reward 合同类）与
  `responsible`（expert/platform/joint/qa_tool）。
- `verdict.failure_classes` 记录五类命中计数，便于按类合并返修。
- 平台侧故障不计入 blocking，但必须写清；避免把平台问题记成专家缺陷。

### 提示词

- `01-qa-manager`：改为先跑机械门禁再派分片；新增五类问题表、四项交付合同、
  `[0.15, 0.8]` 归一化口径、PASS/FAIL 判级纪律、抽检回源要求。
- `02-shard-worker`：新增五个必查位置（评分出口、reward 文件、Oracle 入口、
  上下文与依赖、公开合同），findings 增加 `class`/`responsible`。
- `03-repair-agent`：新增按类修法表与交付底线（体积、离线依赖、可复现、
  非交互、凭证、架构），要求打包后自查体积。
- `00-main-orchestrator`：状态机改为 FAIL→repair，记录新的拒绝条件。

### 其他

- 新增 `docs/`：五份飞书文档快照 + `00-sources.md`（URL、抓取日期、SHA-256、
  群公告要点）。
- `state.json` 升到 `schema_version: 2`，新增 `standard` 字段。
- 默认 skill 路径改为可用 `AUTORESEARCH_QA_SKILL` 覆盖；默认 loop 根改为
  `./loop/<task>`，去掉 Windows 绝对路径。
- `init` 复制 `scripts/` 进循环根，闭环自包含。
- `selftest.py` 覆盖新结论、门禁与体积约束的拒绝路径。

## v1.0.0 — 初始版本

`qa → repair → qa → done` 状态机、哈希闸门、四层角色与工作区隔离。
