# 文档来源与快照说明

本目录是飞书群「A5-autosearch作业群」与平台文档的**只读快照**，用于让本工具的检查口径可追溯。原文以飞书为准；这里不做改写，只做标题归一与索引。

- 群聊：`A5-autosearch作业群`，`chat_id = oc_ac73f8678cb6c1b334eb3932741a4440`
- 抓取时间：2026-11-25（Asia/Shanghai）
- 抓取方式：`lark-cli docs +fetch --doc-format markdown --as user`
- 抓取身份：项目成员本人（用户身份），未使用 bot

## 快照清单

| 文件 | 标题 | 来源 | 抓取日期 | SHA-256 |
|---|---|---|---|---|
| `01-返修指引.md` | 返修指引 | https://bytedance.larkoffice.com/wiki/VoUAwGA8Eil5Tgk70Q4cV9HJnjb | 2026-11-25 | `b69c71ecee15a2e61e71b33de2c2bf47df432d60a7c5bef970d7a1efae6cbbd7` |
| `02-ICM构建环境与Dockerfile兼容性.md` | ICM 构建环境与 Dockerfile 兼容性说明 | https://bytedance.larkoffice.com/wiki/DrcBwlqM8iucMrkHIwtctj4TnIh | 2026-11-25 | `49c99d5a7ec320d2348aef7985fa5dd8b2c6a15e885f5231ebc5788561087c7a` |
| `03-终检问题汇总-20261113.md` | AutoResearch 专家终检问题汇总（2026-11-13） | https://shujufuwubu.feishu.cn/docx/QZJQdgZlyoDNPFxaPemcEYcPnjg | 2026-11-25 | `53a6f50d7436a8fd30337f676a49fb8fe9408589e43a9161a052ff32c2a3c519` |
| `04-验收结算与返修政策.md` | AutoResearch｜题目验收结算以及返修政策 | https://bytedance.larkoffice.com/wiki/WynDwvrLMi4sOTkmXjfcsvKbngd | 2026-11-25 | `d6c328e004d9999ffca01e55fe483a364c52cefb4ae2e57ee39d2caf8c9242af` |
| `05-GPU费用报销政策.md` | GPU 费用报销政策（放宽条件） | https://bytedance.larkoffice.com/wiki/DZmTwGLBCi43lKkkDeVczYWHntf | 2026-11-25 | `71967f98c917ae0f440b295807d6df4a7cc66c9c72431a25b807d413e569cc4f` |

## 群公告中未落盘、但影响操作节奏的信息

摘自同群 2026-11-19 至 2026-11-24 的消息（原文见群公告，未逐条快照）：

- 2026-11-24：题包压缩前 ≤ 50 GiB，镜像 ≤ 50 GiB，单层 ≤ 5 GiB；建议 ≤ 20 GiB / ≤ 10 GiB。体积越大构建越容易超时。
- 2026-11-24：折价验收按简单 400 / 中等 500 / 困难 600 元；可主动结束返修；不限返修次数；最后一批结算前，最后一次返修须在 **2026-12-01 24:00** 前提交。
- 2026-11-20：GPU 费用报销放宽——12-01 24:00 前通过任意一次静态质检即可申请，无需等终检。
- 2026-11-24：终检结论在 11-26 19:00 前每日更新。
- 2026-11-19：终检打回范围 11.14 / 11.17；平台侧问题统一撤回重跑，不占返修次数；专家侧问题附修复建议与返修指引。

## 使用纪律

- 这些文档是**规则来源**，不是结论。检查结论仍以实际包字节与本仓库脚本输出为准。
- 政策类文档（04、05）只影响优先级与结算口径，不改变技术判定；不要拿“折价验收”倒推 PASS。
- 平台侧故障与专家侧缺陷必须分开记录（verdict 的 `responsible` 字段），平台故障不得计入 blocking。
- 文档更新后重新抓取并更新本表 SHA-256；旧快照不覆盖，追加新行。
