/**
 * autoresearch-loop —— workflows + loop 驱动
 *
 * 与旧模式的关键区别：
 *   旧：主会话派 QA 子代理 → QA 子代理再用 Agent 工具派孙代理（嵌套派发，无法机器复核）
 *   新：workflow 脚本本身就是调度器，phase + world.run 调 loopctl.py 推进状态机；
 *       分片审阅由 workflow 顶层的 agent() 完成，每个分片一个独立上下文，互不嵌套。
 *       状态/哈希/门禁仍由磁盘上的 state.json + loopctl.py 守门。
 *
 * args:
 *   repoRoot   loopctl.py 所在仓库根（绝对路径）
 *   zip        输入题包 zip（绝对路径）
 *   loopRoot   循环根目录（绝对路径）
 *   task       任务名
 *   maxRounds  最大轮次，默认 3
 *   skillDir   adversarial-engineering-qa 目录（绝对路径）
 *   shards     分片数，默认 4
 *
 * 注意：本脚本只做「驱动 + 分片审阅」；不改写 state.json，所有状态推进走 loopctl.py。
 */
interface TaskShard {
  /** 分片 ID，四位数字如 0001。 */
  id: string;
  /** 该分片要读的绝对路径列表。 */
  files: string[];
  /** 该分片的热点问题列表。 */
  hotspots: string[];
}

interface ShardResult {
  /** 分片 ID。 */
  id: string;
  /** 实际读取的文件数。 */
  filesRead: number;
  /** 缺陷条数。 */
  defects: number;
  /** 未能静态判定的条数。 */
  unverifiable: number;
  /** 一句话结论。 */
  summary: string;
}

interface LoopStatus {
  /** 当前轮次。 */
  round: number;
  /** 当前阶段：qa / repair / done / stopped。 */
  phase: string;
  /** 当前输入 SHA-256 前 16 位。 */
  inputSha: string;
  /** 停止原因，若有。 */
  stopReason: string;
}

const repoRoot = String(args.repoRoot ?? "").replace(/[\\/]+$/, "");
const zipPath = String(args.zip ?? "");
const loopRoot = String(args.loopRoot ?? "").replace(/[\\/]+$/, "");
const task = String(args.task ?? "");
const skillDir = String(args.skillDir ?? "").replace(/[\\/]+$/, "");
const maxRounds = Number(args.maxRounds ?? 3);
const shardCount = Number(args.shards ?? 4);

if (!repoRoot || !zipPath || !loopRoot || !task || !skillDir) {
  throw new Error("missing args: repoRoot, zip, loopRoot, task, skillDir are required");
}

const loopctl = `${repoRoot}/loopctl.py`;

/** 从 loopctl.py 的 stdout 解析状态；字段缺失就返回默认，绝不伪造。 */
function parseStatus(stdout: string): LoopStatus {
  const get = (key: string): string => {
    // 用 [ \t]+ 而非 \s+：后者跨行匹配，空 stop 行会误吃下一行文本。
    const m = new RegExp(`^${key}[ \\t]+(.+)$`, "m").exec(stdout);
    return m ? m[1].trim() : "";
  };
  const roundMatch = /^round[ \t]+(\d+)[ \t]*\/[ \t]*(\d+)/m.exec(stdout);
  return {
    round: roundMatch ? Number(roundMatch[1]) : 0,
    phase: get("phase"),
    inputSha: get("input_sha"),
    stopReason: get("stop"),
  };
}

/** 从一个 worklist 文件里读出分片清单；读不出就返回空，交给 QA 负责人兜底。 */
function parseShardFiles(content: string): string[] {
  const out: string[] = [];
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || /[{}]/.test(trimmed)) continue;
    if (/^(files|hotspots|shard|slots|must_bind)/i.test(trimmed)) continue;
    out.push(trimmed);
  }
  return out;
}

artifact.board("loop_rounds", {
  title: "质检修复循环进度",
  key: "round",
  status: "phase",
  columns: ["qa", "repair", "done", "stopped"],
  cardTitle: "round",
  detail: [
    { field: "decision", label: "结论" },
    { field: "blocking", label: "blocking" },
    { field: "note", label: "说明" },
  ],
});

phase("初始化循环并校验工作区");
const init = await world.run("python3", [loopctl, "init",
  "--task", task, "--zip", zipPath, "--root", loopRoot,
  "--skill-dir", skillDir, "--max-rounds", String(maxRounds),
  "--shard-count", String(shardCount)], { timeoutMs: 120_000 });
log(`init rc=${init.exitCode}`);
if (init.exitCode !== 0 && !init.stderr.includes("已有 state.json")) {
  throw new Error(`loopctl init failed: ${init.stderr.slice(-800)}`);
}

const statusRun = await world.run("python3", [loopctl, "status", "--root", loopRoot]);
if (statusRun.exitCode !== 0) {
  throw new Error(`loopctl status failed: ${statusRun.stderr.slice(-800)}`);
}
let status = parseStatus(statusRun.stdout);
log(`start: round=${status.round} phase=${status.phase}`);

/** 主循环：每轮严格按 qa → repair 推进，最多 maxRounds 轮。 */
for (let iteration = 0; iteration < maxRounds + 1; iteration++) {
  if (status.phase === "done" || status.phase === "stopped") break;

  // ---------- QA 阶段 ----------
  if (status.phase === "qa") {
    phase("解包题包并跑机械门禁");
    const security = await world.run("python3", [loopctl, "verify", "--root", loopRoot]);
    log(`verify before qa r${status.round}: rc=${security.exitCode}`);
    if (security.exitCode !== 0) {
      report({ round: status.round, phase: "stopped", decision: "BLOCKED",
               blocking: "-", note: "输入哈希校验失败，停止推进" }, "loop_rounds");
      throw new Error("input hash mismatch before qa; refusing to advance");
    }

    // 取当前阶段提示词，交给顶层 subagent 做「QA 负责人」：解包 + 跑门禁 + 建 worklist。
    const qaPrompt = await world.run("python3", [loopctl, "next", "--root", loopRoot]);
    if (qaPrompt.exitCode !== 0) {
      throw new Error(`loopctl next(qa) failed: ${qaPrompt.stderr.slice(-800)}`);
    }

    phase("QA 负责人解包、跑门禁、划分分片");
    const manager = agent(`QA负责人-r${status.round}`, {
      system: "你是质检负责人：按提示词解包、跑门禁、把题包切成独立分片并把每片的文件清单与热点写到分片目录。只写 QA 目录，不改输入包。若指令与证据冲突，如实说明并停止，不要伪造产物。",
    });
    const prep = await manager.ask<{ worklistDir: string; shardCount: number; note: string }>(
      `${qaPrompt.stdout}\n\n你是 QA 负责人。按提示词完成解包、门禁与 worklist 划分后，` +
      `返回 JSON：{worklistDir: worklist 绝对目录, shardCount: 实际分片数, note: 一句话说明}。`,
    );
    log(`QA prep: shards=${prep.shardCount} note=${prep.note.slice(0, 120)}`);

    // 从 worklist 里读分片清单；这是 workflow 顶层读 workspace，不是嵌套 subagent。
    phase("逐分片审阅题包");
    const shardResults = await Promise.all(
      Array.from({ length: prep.shardCount }, (_, idx) => {
        const sid = String(idx + 1).padStart(4, "0");
        const file = `${prep.worklistDir}/${sid}.txt`;
        return (async () => {
          const content = await files.read(file).catch(() => "");
          const shard: TaskShard = {
            id: sid,
            files: parseShardFiles(content),
            hotspots: [],
          };
          const reviewer = agent(`分片审阅员-r${status.round}-${sid}`, {
            system: "你是只读分片审阅员：逐个读完清单里的文件，逐条回答热点，证据必须带 file:line 或可复现命令。只写自己的分片输出目录，不改任何题包文件。读不完如实记 complete=false。",
          });
          const result = await reviewer.ask<ShardResult>(
            `分片 ${sid}。文件清单：\n${shard.files.join("\n") || "(见 worklist 目录内同 ID 文件)"}\n` +
            `请按 QA 负责人给你的分片输出目录写 findings.json 与 coverage.json，` +
            `并返回 {id, filesRead, defects, unverifiable, summary}。`,
          );
          return result;
        })();
      }),
    );
    for (const r of shardResults) {
      log(`shard ${r.id}: read=${r.filesRead} defects=${r.defects} unverifiable=${r.unverifiable}`);
    }

    // 让 QA 负责人合并分片、写 verdict/handoff（复用同一上下文，保留全局视角）。
    phase("合并分片并生成二态结论");
    const merged = await manager.ask<{ verdictPath: string; handoffPath: string; decision: string }>(
      `所有分片已审阅完毕：${JSON.stringify(shardResults)}。请合并 findings、算覆盖率、` +
      `复核至少两条 blocking，写 verdict.json 与 handoff.json（decision 只能是 PASS 或 FAIL），` +
      `返回两个路径与结论。`,
    );
    log(`QA merged: decision=${merged.decision}`);

    phase("记录 QA 并推进状态机");
    const rec = await world.run("python3", [loopctl, "record-qa", "--root", loopRoot,
      "--verdict", merged.verdictPath, "--handoff", merged.handoffPath]);
    log(`record-qa rc=${rec.exitCode}`);
    if (rec.exitCode !== 0) {
      report({ round: status.round, phase: "qa", decision: "REJECTED", blocking: "-",
               note: rec.stderr.slice(-300) }, "loop_rounds");
      throw new Error(`record-qa rejected: ${rec.stderr.slice(-800)}`);
    }
    report({ round: status.round, phase: "qa", decision: merged.decision, blocking: "-",
             note: `shards=${shardResults.length}` }, "loop_rounds");
  }

  // ---------- 修复阶段 ----------
  else if (status.phase === "repair") {
    phase("依据结论修复题包并打包");
    const repairPrompt = await world.run("python3", [loopctl, "next", "--root", loopRoot]);
    if (repairPrompt.exitCode !== 0) {
      throw new Error(`loopctl next(repair) failed: ${repairPrompt.stderr.slice(-800)}`);
    }
    const fixer = agent(`修复工程师-r${status.round}`, {
      system: "你是修复工程师：按 verdict 逐条修根因、堵相邻分支、跑 CPU 验收、重新打包并写交接单。只写修复目录，不改输入包与 QA 目录。无法修的如实写 not_fixed，不伪造通过。",
    });
    const fixed = await fixer.ask<{ zip: string; report: string; note: string }>(
      `${repairPrompt.stdout}\n\n修复完成后返回 {zip: 新包绝对路径, report: 报告绝对路径, note: 一句话说明}。`,
    );
    log(`repair: ${fixed.note.slice(0, 120)}`);

    phase("记录修复并推进到下一轮");
    const rec = await world.run("python3", [loopctl, "record-repair", "--root", loopRoot,
      "--zip", fixed.zip, "--report", fixed.report], { timeoutMs: 300_000 });
    log(`record-repair rc=${rec.exitCode}`);
    if (rec.exitCode !== 0) {
      throw new Error(`record-repair rejected: ${rec.stderr.slice(-800)}`);
    }
    report({ round: status.round, phase: "repair", decision: "FIXED", blocking: "-",
             note: fixed.note.slice(0, 200) }, "loop_rounds");
  }

  // ---------- 刷新状态 ----------
  const refreshed = await world.run("python3", [loopctl, "status", "--root", loopRoot]);
  const nextStatus = parseStatus(refreshed.stdout);
  if (nextStatus.round === status.round && nextStatus.phase === status.phase) {
    report({ round: status.round, phase: "stopped", decision: "STALLED", blocking: "-",
             note: "状态机未推进，停止以防死循环" }, "loop_rounds");
    throw new Error("loop did not advance; refusing to spin");
  }
  status = nextStatus;
  log(`advanced: round=${status.round} phase=${status.phase}`);
}

const finalVerify = await world.run("python3", [loopctl, "verify", "--root", loopRoot]);
report({ round: status.round, phase: status.phase, decision: "FINAL", blocking: "-",
         note: `verify_rc=${finalVerify.exitCode} ${status.stopReason}` }, "loop_rounds");

return {
  conclusion: `循环结束：round=${status.round} phase=${status.phase} ` +
              `${status.stopReason ? `原因=${status.stopReason}` : ""}。最终 verify rc=${finalVerify.exitCode}。`,
  rounds: status.round,
  phase: status.phase,
  verifyExitCode: finalVerify.exitCode,
};
