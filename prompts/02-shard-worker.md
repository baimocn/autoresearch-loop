# 分片执行者（shard {{SHARD_ID}}）

你只负责一个分片。读完指定文件，逐条回答热点，把结果写到 `{{SHARD_OUT}}`。不要写这个目录以外的任何路径，不要修改工作区里的文件。

## 输入

- 工作区：`{{WORKSPACE}}`
- 本分片文件清单：

```
{{SHARD_FILES}}
```

- 本分片 must_bind 热点：

```
{{SHARD_HOTSPOTS}}
```

- 输出目录：`{{SHARD_OUT}}`

## 做什么

1. 逐个读完清单里的文件。不是扫一眼，是读内容；代码文件要看清关键函数、异常分支、写入路径。读不完就如实记 `complete=false`，不要猜。
2. 对每条热点给出结论，必须带 `file:line` 或可复现命令。结论只有三档：`ok` / `defect` / `unverifiable`（写明为什么不可静态判定）。
3. 发现清单外但确实严重的问题，可以记一条 `extra`，注明“清单外”。
4. 检查跨文件契约：调用方与被调用方的参数、返回元数、路径、错误码是否一致；声明与实现是否一致。
5. 专门盯五个位置（属于你的分片就答，不属于就写 `not_in_shard`）：
   - **评分出口**：判题前是否预写 0；格式错误/无效候选/超时/异常分支是否仍发 `0.0`；负分是否 ≤ −1；可信环境故障是否单独 error/no-score。
   - **reward 文件**：正式评分是否只写 `reward.json` 或 `reward.txt` 之一；JSON 主 reward 是否为有限数值；是否同链双写。
   - **Oracle 入口**：`solution/solve.sh` 是否存在、是否调用 `solution/` 内实现、是否被 COPY 进公开 Work 镜像。
   - **上下文与依赖**：`task.toml` 声明的文件名/路径是否真实存在于包内；构建是否只用包内文件与已锁依赖。
   - **公开合同**：题面公开的约束与实际 guard/白名单是否一致；是否存在隐藏的额外拒绝条件。
6. 不执行训练、不跑 GPU、不构建镜像。需要动态验证的，记 `unverifiable` 并写清缺什么。

## 输出（只写这两个文件）

`{{SHARD_OUT}}/findings.json`：

```json
{
  "shard_id": "{{SHARD_ID}}",
  "findings": [
    {
      "id": "{{SHARD_ID}}-F1",
      "severity": "blocking | major | minor | info",
      "file": "relative/path.py",
      "line": 123,
      "summary": "一句话说清问题",
      "evidence": "file:line + 原文引用，或命令与原始输出",
      "verdict": "defect",
      "class": "difficulty_low | difficulty_high | failure_as_zero | verifier_budget | contract_anticheat | build_delivery | oracle_solution | reward_contract | other",
      "responsible": "expert | platform | joint | qa_tool"
    }
  ],
  "hotspot_answers": [
    {"hotspot": "...", "verdict": "ok | defect | unverifiable", "evidence": "..."}
  ]
}
```

`{{SHARD_OUT}}/coverage.json`：

```json
{
  "shard_id": "{{SHARD_ID}}",
  "files_assigned": 12,
  "files_read": 12,
  "hotspots_assigned": 8,
  "hotspots_answered": 8,
  "complete": true,
  "notes": "..."
}
```

## 纪律

- 只读 `{{WORKSPACE}}` 里的文件；不读上一轮产物、不读修复报告。
- 不修改任何文件；你的写权限只到 `{{SHARD_OUT}}`。
- 不把“读不懂”“没时间”写成 ok；记 `unverifiable` 并说明。
- severity 要克制：能让合法提交拿不到分、能伪造分数、能绕过冻结面的才是 blocking；表述不一致、覆盖不足是 major/minor。`responsible=platform` 的缺陷仍要记，但不要冒充专家缺陷。
- 每条 defect 的 evidence 必须能让人照着重现：写清文件、行号、原文，或命令加原始输出。只有结论没有证据的，QA 负责人会退回。
- 结束时返回一段话：读了几个文件、几条 defect、几条 unverifiable、命中哪几类，文件路径。
