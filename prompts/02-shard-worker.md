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
5. 不执行训练、不跑 GPU、不构建镜像。需要动态验证的，记 `unverifiable` 并写清缺什么。

## 输出（只写这两个文件）

`{{SHARD_OUT}}\findings.json`：

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
      "evidence": "代码片段 / 命令 / 复现步骤",
      "verdict": "defect"
    }
  ],
  "hotspot_answers": [
    {"hotspot": "...", "verdict": "ok | defect | unverifiable", "evidence": "..."}
  ]
}
```

`{{SHARD_OUT}}\coverage.json`：

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
- severity 要克制：能让合法提交拿不到分、能伪造分数、能绕过冻结面的才是 blocking；文档表述不一致、覆盖不足是 major/minor。
- 结束时返回一段话：读了几个文件、几条 defect、几条 unverifiable，文件路径。
