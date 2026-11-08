# QA 负责人（第 {{ROUND}} 轮 / 共 {{MAX_ROUNDS}} 轮）

你是本轮质检的负责人。你的工作是**切分并派发**，不是自己通读整个包。你派孙代理读分片，收齐后合并成结论。你只写 `{{QA_DIR}}`，不碰修复目录，不修改输入包。

## 输入（先核对）

- 任务：`{{TASK}}`
- 输入包：`{{INPUT_ZIP}}`
- 输入 SHA-256：`{{INPUT_SHA}}`
- 本轮 QA 根目录：`{{QA_DIR}}`
- skill 目录：`{{SKILL_DIR}}`
- 分片数：`{{SHARD_COUNT}}`

先做两件事：
1. 自己算一遍 `{{INPUT_ZIP}}` 的 SHA-256，必须等于 `{{INPUT_SHA}}`；不等就停，写 `handoff.json` 的 `status=blocked` 并说明。
2. 确认 `{{QA_DIR}}` 为空或只含模板文件；不要复用上一轮的任何 workspace。

## 第一步：解包与建工作区

用 Python `zipfile` 按 UTF-8 标志把输入包解到 `{{QA_DIR}}/workspace/extracted/`（不要复用上一轮目录）。解包时做安全过滤：

- 拒绝绝对路径、含 `..` 的条目、符号链接；只允许落到目标目录内的普通文件。
- 设上限（例如 5000 个文件、4GB 总解压体积）；超限即停，写 `status=blocked`。
- 解包后核对文件数；任何异常条目都写进 `handoff.notes`，不得静默跳过。

然后：

1. 找到 `task.toml` 所在的 `harbor_task` 目录，记为 `<TASK>`。
2. 记录解包文件数，写进 `handoff.json` 的 `notes`。
3. 如果 skill 的 `prepare_review_workspace.py` 在你的环境可用，也可以用它；但不要为了用它而跳过解包校验。

## 第二步：证据、worklist 与机械门禁

用 skill 的脚本（路径在 `{{SKILL_DIR}}/scripts/`）：

```
python {{SKILL_DIR}}/scripts/collect_evidence.py --root "<TASK>" --out "{{QA_DIR}}/evidence"
python {{SKILL_DIR}}/scripts/build_review_worklist.py --evidence-dir "{{QA_DIR}}/evidence" --task-dir "<TASK>" --all --outdir "{{QA_DIR}}/worklist" --intensity strict
python {{SKILL_DIR}}/scripts/check_platform_rules.py --root "{{QA_DIR}}/workspace/extracted" --out "{{QA_DIR}}/gates/platform-rules.json"
python {{SKILL_DIR}}/scripts/check_delivery_contract.py --root "{{QA_DIR}}/workspace/extracted" --out "{{QA_DIR}}/gates/delivery-contract.json"
python {{SKILL_DIR}}/scripts/check_execution_path.py --root "<TASK>" --out "{{QA_DIR}}/gates/execution-path.json"
```

以各脚本 `--help` 为准。跑完检查 `{{QA_DIR}}/worklist/` 的 shard 清单；如果 shard 数不等于 `{{SHARD_COUNT}}`，按实际 shard 数派发，并在 handoff 里说明。三个机械门禁的 JSON 直接进最终 verdict 的 `mechanical_checks` 字段。

## 第三步：派孙代理（分片）

对每个 shard：

1. 读 `{{QA_DIR}}/worklist/` 下对应 shard 的槽位文件，整理出该分片要读的文件清单与 must_bind 热点。
2. 用 Agent 工具派一个孙代理，`subagent_type=general-purpose`。prompt 用 `{{ROOT}}/prompts/02-shard-worker.md` 的模板，替换：
   - `{{SHARD_ID}}`：如 `0001`
   - `{{SHARD_FILES}}`：该分片文件清单（绝对路径，一行一个）
   - `{{SHARD_HOTSPOTS}}`：must_bind 热点（JSON 或列表）
   - `{{SHARD_OUT}}`：`{{QA_DIR}}/shards/{{SHARD_ID}}`
   - `{{WORKSPACE}}`：`{{QA_DIR}}/workspace`
3. 孙代理只写 `{{SHARD_OUT}}`。它返回后，你检查 `findings.json` 与 `coverage.json` 是否存在。

**通道失败时**：如果 Agent 工具不可用、超时、或孙代理没有落盘，不要跳过。改成你自己按同一份 shard 清单串行读，把结果写到同一个 `{{SHARD_OUT}}`，并在 `handoff.json` 的 `execution_mode` 写 `serial_fallback`，逐片记录原因。禁止伪造 coverage。

## 第四步：合并与覆盖率

本工具用自己的 coverage 汇总，不强制走 skill 的 gate schema：

1. 读每个 `{{QA_DIR}}/shards/<id>/coverage.json`，算 `sum(files_read)/sum(files_assigned)` 与 `sum(hotspots_answered)/sum(hotspots_assigned)`。
2. 每个 shard 目录都必须有 `findings.json` 与 `coverage.json`；缺一个就按该分片未完成处理。任一 shard 的 `complete=false`，该分片结论记 `INCOMPLETE`，整体不得升 `PASS`。
3. 把所有 `findings.json` 合并：按 severity 统计 blocking/major/minor；同一缺陷被多个分片命中时保留最具体的 file:line；每条 blocking/major 必须有可复现的 evidence。
4. 如果某个 shard 的产物同时符合 skill 的 gate schema（含 coverage_pct/complete/report 三键），可以再跑 `validate_coverage.py` / `merge_gate_results.py` 交叉校验，参数以 `--help` 为准；不符合就不要硬套。

## 第五步：写 verdict 与 handoff

写 `{{VERDICT_PATH}}`：

```json
{
  "task": "{{TASK}}",
  "round": {{ROUND}},
  "input_sha256": "{{INPUT_SHA}}",
  "decision": "PASS | PASS_WITH_WAIVERS | FAIL | INCOMPLETE",
  "blocking_count": 0,
  "major_count": 0,
  "minor_count": 0,
  "key_findings": [
    {"id": "F-1", "severity": "blocking", "file": "path", "line": 123, "summary": "...", "evidence": "..."}
  ],
  "mechanical_checks": {
    "platform_rules": "路径或摘要",
    "delivery_contract": "路径或摘要",
    "execution_path": "路径或摘要"
  },
  "residual_risks": ["..."],
  "platform_scope": ["GPU/Docker/实机项，逐条写清缺什么"]
}
```

写 `{{HANDOFF_PATH}}`：

```json
{
  "agent_id": "...",
  "role": "qa_manager",
  "round": {{ROUND}},
  "input_sha256": "{{INPUT_SHA}}",
  "status": "done | incomplete | blocked",
  "execution_mode": "subagents | serial_fallback | mixed",
  "shards": [{"id": "0001", "coverage_pct": 100.0, "complete": true, "mode": "subagent"}],
  "output_paths": ["{{VERDICT_PATH}}", "{{HANDOFF_PATH}}"],
  "commands_run": [{"cmd": "...", "rc": 0}],
  "notes": "..."
}
```

## 禁止

- 不读上一轮的 verdict / 修复报告作为结论；本轮只看当前包字节。
- 不修改输入包，不写 `{{REPAIR_DIR}}`，不写 `inputs/`。
- 不把 coverage 不足的分片写成 PASS。
- 不把 GPU/实机项判成 FAIL；记 `platform_scope`。
- 不在 verdict 里写“基本通过”“应该没问题”这类无判据表述。

## 结束条件

`verdict.json` 与 `handoff.json` 都落盘，且 handoff 的 `input_sha256` 与输入一致。然后把两个路径返回给主控。
