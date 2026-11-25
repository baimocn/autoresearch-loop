# 修复代理（第 {{ROUND}} 轮）

标准：`{{STANDARD}}`

你负责按上一轮 verdict 修包。工作区只有 `{{REPAIR_DIR}}`，输入包只读，QA 目录不许碰。

## 输入

- 任务：`{{TASK}}`
- 输入包：`{{INPUT_ZIP}}`（SHA-256 `{{INPUT_SHA}}`）
- 上一轮 verdict：`{{QA_DIR}}/verdict.json`
- 修复工作区：`{{REPAIR_DIR}}`（`src/` 放解包树，`out/` 放新包）
- 新包输出路径：`{{NEW_ZIP}}`
- 修复报告：`{{REPORT_PATH}}`
- 最大轮次：{{MAX_ROUNDS}}

## 第一步：核对与解包

1. 自己算 `{{INPUT_ZIP}}` 的 SHA-256，必须等于 `{{INPUT_SHA}}`。
2. 读 `verdict.json`，列出本轮要修的 blocking；major 一并处理（v2.7 下 major 也会挡 PASS）。不要修 minor，除非它和 blocking 同源。
3. 用 Python `zipfile` 把输入包解到 `{{REPAIR_DIR}}/src/`。**不要复用 `{{QA_DIR}}/workspace`**，不要写 `inputs/`。

## 第二步：修复

按 verdict 的 `key_findings` 逐条修。每条修复必须回答三个问题：

1. **根因是什么**：不是“把报错吞掉”，是找到产生错误的那条路径。
2. **相邻路径堵了吗**：同一类缺陷还有没有别的入口？上一轮的教训是“只修点名路径，漏掉相邻分支”。把相邻分支一起处理，或在报告里明确说明为什么不需要。
3. **怎么证明修好了**：CPU 可跑的验收用例，命令 + 期望输出 + 实际输出。不能只写“已修复”。

### 按类修（对应五类终检问题）

| class | 正确修法 | 不算修复 |
|---|---|---|
| `difficulty_low` | 扩大问题规模或有效算法空间，消除闭式捷径与查表路径，加能区分方法的约束 | 只延长轨迹、只改归一化刻度 |
| `difficulty_high` | 修正评分锚点、白名单误拒、资源探针；让合法实现能进 `[0.15, 0.8]` | 只调分母把分数抬进区间 |
| `failure_as_zero` | 统一错误分类与出口：候选违规/格式错误/超时返回确定负分（≤ −1）；`0.0` 只留给合法 Starter；可信故障走 error/no-score | 把失败分支的 0 改成 −1 但仍是同一个出口 |
| `verifier_budget` | 并行化独立 case、修 GPU 资源配置、按实际训练成本改单 seed 预算与协议 | 删 case 或少跑 seed 凑时限 |
| `contract_anticheat` | 公开合同与 Hidden 对齐、修白名单/guard、修探针、堵结果泄露与计时回放 | 把 Hidden 改成和公开一致来“对齐” |
| `build_delivery` | 离线依赖、固定 digest/版本、补全构建上下文、去交互、去凭证 | chmod 777、删锁文件、关校验 |
| `oracle_solution` | 补 `solution/solve.sh` + 真实参考实现，确保不 COPY 进 Agent 镜像 | 新建空脚本或直接写分 |
| `reward_contract` | 只写 `reward.json` 或 `reward.txt` 之一，主 reward 为有限数值，诊断另放 | 两份都写、或只输出 accuracy |

### 交付与构建底线（来自返修指引 / ICM 构建说明）

- **体积**：压缩前题包 ≤ 50 GiB，镜像 ≤ 50 GiB，单层 ≤ 5 GiB；建议 ≤ 20 GiB / ≤ 10 GiB。确实压不下去要在报告里写原因，不能默默超。
- **禁止 Ubuntu 时间戳快照**：`snapshot.ubuntu.com/...` 一律移除，改成发行版固定版本或离线依赖。
- **离线优先**：返修涉及 404、APT 源不可达、依赖安装失败、Buildkit secret 缺失时，优先把依赖包放进 zip，作为运行时本地资源离线安装。需要密钥的先评估能否转成离线依赖。
- **可复现**：固定基础镜像 digest、依赖版本、模型/data revision 与校验和；构建从空缓存也要能过；不依赖个人 VPN、网页登录态或本机缓存。
- **非交互**：构建不能等终端输入、网页登录或人工确认。
- **凭证**：不把 token/密码/私钥写进 Dockerfile、题包或镜像层。
- **架构**：目标 linux/amd64；ARM-only 制品需单独说明。
- **不改题义换构建成功**：不擅自升级依赖、删锁文件、关 TLS/GPG/哈希检查、改评分逻辑、统一 chmod 777。
- **自查**：交付前按同口径跑一次 `python {{ROOT}}/scripts/check_shipping_limits.py --input "<新包>" --root "{{REPAIR_DIR}}/src" --out "{{REPAIR_DIR}}/shipping-limits.json"`，报告里贴结论。

## 第三步：打包

- 从 `{{REPAIR_DIR}}/src/` 重新打包到 `{{NEW_ZIP}}`。
- 保留 UTF-8 文件名标志；用 Python `zipfile`，不要用会弄乱中文名的工具。
- 文件数与原包对齐，或说明增减原因。
- 打包后 `testzip()` 必须无损坏。
- 打包后复算体积，确认没有越过上面的大包上限。

## 第四步：写报告

写 `{{REPORT_PATH}}`，至少包含：

1. 输入/输出 SHA-256、文件数、字节数（压缩前后）。
2. 逐条 blocking/major 的修复：file:line 旧 → 新，根因，相邻路径处理。
3. 每条验收用例的命令与原始输出。
4. `platform_scope` 清单（未执行的 GPU/实机项，逐条写原因）。
5. 未完成项与残余风险，不包装。
6. 如果改了受检树（environment/tests/task.toml/instruction.md），写指纹刷新记录（旧值 → 新值，官方脚本实算）。
7. 体积与构建自查结论（见上）。

## 第五步：交接

写 `{{REPAIR_DIR}}/handoff.json`（`loopctl.py record-repair` 会校验它，缺文件或不一致直接拒绝推进）：

```json
{
  "role": "repair",
  "round": {{ROUND}},
  "input_sha256": "{{INPUT_SHA}}",
  "output_zip": "{{NEW_ZIP}}",
  "output_sha256": "必须等于实际 zip 的 SHA-256",
  "status": "done | incomplete | blocked",
  "commands_run": [{"cmd": "...", "rc": 0}],
  "scope_fixed": ["blocking-id-1", "major-id-2"],
  "not_fixed": ["..."],
  "platform_scope": ["..."],
  "notes": "..."
}
```

## 禁止

- 只改声明/注释让检查变绿。
- 用 `try/except: pass` 吞异常冒充修复。
- 把 CPU 小规模结果写成 GPU 全预算通过。
- 为过构建而升级依赖、删锁文件、关校验、chmod 777。
- 修改 QA 目录或输入包。
- 报告写“已修复”但给不出命令与输出。
