import React, { useCallback, useEffect, useState } from "react";
import { Play, RefreshCw, Search, Trash2 } from "lucide-react";
import {
  api,
  EvalRunDetail,
  EvalRunSummary,
  formatLocalTime,
  InstanceItem,
  KbDocItem,
  KbSearchResponse,
  TraceListResponse,
} from "../api/client";
import { InstanceCard } from "../components/InstanceCard";
import { Navbar } from "../components/Navbar";
import { StatusBadge } from "../components/StatusBadge";

const PRESET_OPTIONS = [
  { value: "healthy", label: "健康正常 (healthy)" },
  { value: "nginx_502", label: "Nginx 502 叠加故障 (nginx_502)" },
  { value: "port_8080_blocked", label: "8080 端口未放行 (port_8080_blocked)" },
  { value: "icp_unfiled", label: "北京 80 未备案 (icp_unfiled)" },
  { value: "instance_stopped", label: "实例已停机 (instance_stopped)" },
];

export const AdminEvalPage: React.FC = () => {
  const [tab, setTab] = useState<"traces" | "eval" | "kb" | "faults">("traces");

  // Trace 状态（默认优先展示非评测用户流量，避免历史评测跑批淹没真实会话）
  const [traceData, setTraceData] = useState<TraceListResponse | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [traceMsg, setTraceMsg] = useState<string | null>(null);
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [intentFilter, setIntentFilter] = useState("");
  const [decisionFilter, setDecisionFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | "user" | "eval">("user");
  const [offset, setOffset] = useState(0);
  const pageSize = 15;

  // 评测状态
  const [evalRuns, setEvalRuns] = useState<EvalRunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvalRunDetail | null>(null);
  const [evalName, setEvalName] = useState("黄金集基准评测");
  const [evalLimit, setEvalLimit] = useState<string>("");
  const [evalMsg, setEvalMsg] = useState<string | null>(null);
  const [evalErr, setEvalErr] = useState<string | null>(null);
  const [evalBusy, setEvalBusy] = useState(false);

  // 知识库检索预览
  const [kbDocs, setKbDocs] = useState<KbDocItem[]>([]);
  const [kbQuery, setKbQuery] = useState("Nginx 502 怎么排查");
  const [kbTopK, setKbTopK] = useState(4);
  const [kbMinScore, setKbMinScore] = useState(0.35);
  const [kbResult, setKbResult] = useState<KbSearchResponse | null>(null);
  const [kbMsg, setKbMsg] = useState<string | null>(null);
  const [kbErr, setKbErr] = useState<string | null>(null);

  // 故障靶场
  const [instances, setInstances] = useState<InstanceItem[]>([]);
  const [faultMsg, setFaultMsg] = useState<string | null>(null);
  const [faultErr, setFaultErr] = useState<string | null>(null);
  const [loadingInit, setLoadingInit] = useState(true);

  const loadTraces = useCallback(async () => {
    setTraceError(null);
    try {
      const data = await api.listTraces({
        limit: pageSize,
        offset,
        intent: intentFilter || undefined,
        decision: decisionFilter || undefined,
        source: sourceFilter,
      });
      setTraceData(data);
    } catch (e: any) {
      setTraceError(e?.message || "Trace 接口暂不可用");
    }
  }, [offset, intentFilter, decisionFilter, sourceFilter]);

  const loadEvalRuns = useCallback(async () => {
    setEvalErr(null);
    try {
      setEvalRuns(await api.listEvalRuns());
    } catch (e: any) {
      setEvalErr(e?.message || "加载评测历史失败");
    }
  }, []);

  const loadKbDocs = useCallback(async () => {
    setKbErr(null);
    try {
      setKbDocs(await api.listKbDocs());
    } catch (e: any) {
      setKbErr(e?.message || "加载知识库文档列表失败");
    }
  }, []);

  const loadInstances = useCallback(async () => {
    setFaultErr(null);
    try {
      setInstances(await api.listInstances());
    } catch (e: any) {
      setFaultErr(e?.message || "加载实例列表失败");
    }
  }, []);

  useEffect(() => {
    let active = true;
    setLoadingInit(true);
    Promise.all([loadTraces(), loadEvalRuns(), loadKbDocs(), loadInstances()]).finally(
      () => {
        if (active) setLoadingInit(false);
      }
    );
    return () => {
      active = false;
    };
  }, [loadTraces, loadEvalRuns, loadKbDocs, loadInstances]);

  // 存在运行中的评测批次时自动每 3 秒轮询刷新进度
  const hasRunningEval = evalRuns.some((r) => r.status === "running");
  useEffect(() => {
    if (!hasRunningEval) return;
    const timer = window.setInterval(() => {
      loadEvalRuns();
      loadTraces();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasRunningEval, loadEvalRuns, loadTraces]);

  const handleRunSearchPreview = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!kbQuery.trim()) return;
    setKbMsg(null);
    setKbErr(null);
    try {
      setKbResult(await api.searchKbPreview(kbQuery.trim(), kbTopK, kbMinScore));
    } catch (err: any) {
      setKbErr(err?.message || "检索预览失败");
    }
  };

  const handleReindex = async () => {
    setKbMsg(null);
    setKbErr(null);
    try {
      const r = await api.reindexKb();
      setKbMsg(`全量重建索引完成：${r.documents} 篇文档 / ${r.chunks} 个切片`);
      await loadKbDocs();
    } catch (err: any) {
      setKbErr(err?.message || "重建失败");
    }
  };

  const handleApplyPreset = async (instId: number, preset: string) => {
    setFaultMsg(null);
    setFaultErr(null);
    try {
      const updated = await api.applyFaultPreset(instId, preset);
      setFaultMsg(
        `实例 ${updated.instance_name} 已切换至预设「${preset}」（状态：${updated.status}，端口：${updated.firewall_ports.join(", ")}）`
      );
      await loadInstances();
    } catch (err: any) {
      setFaultErr(err?.message || "切换预设失败");
    }
  };

  const handleTriggerEval = async () => {
    setEvalBusy(true);
    setEvalMsg(null);
    setEvalErr(null);
    try {
      const limitNum = evalLimit.trim() ? Number(evalLimit.trim()) : undefined;
      const res = await api.triggerEvalRun({
        run_name: evalName.trim() || "黄金集评测",
        limit: limitNum,
      });
      setEvalMsg(`评测任务 #${res.run_id} 已在后台启动，页面将每 3 秒自动刷新进度。`);
      await loadEvalRuns();
    } catch (err: any) {
      setEvalErr(err?.message || "触发评测失败");
    } finally {
      setEvalBusy(false);
    }
  };

  const handleCleanupEvalTraces = async () => {
    setCleanupBusy(true);
    setTraceMsg(null);
    setTraceError(null);
    try {
      const res = await api.cleanupTraces({ source: "eval", older_than_days: 0 });
      setTraceMsg(
        `已清理 ${res.deleted_count} 条评测跑批 Trace（剩余非评测流量 ${res.remaining_user_calls} 条）`
      );
      setOffset(0);
      await loadTraces();
    } catch (err: any) {
      setTraceError(err?.message || "清理评测 Trace 失败");
    } finally {
      setCleanupBusy(false);
    }
  };

  const pct = (v?: number | null) => (v == null ? "-" : `${(v * 100).toFixed(1)}%`);

  return (
    <div className="min-h-screen flex flex-col bg-[#F7F8FA]">
      <Navbar />

      {/* 二级下划线导航 */}
      <div className="h-11 px-6 bg-white border-b border-[#E5E7EB] flex items-center gap-6">
        {(
          [
            ["traces", "全链路 Trace 看板"],
            ["eval", "32 题黄金集评测"],
            ["kb", "知识库检索预览"],
            ["faults", "实例故障注入靶场"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`relative h-full text-[13px] transition-colors ${
              tab === key
                ? "text-[#111827] font-semibold"
                : "text-[#6B7280] hover:text-[#111827]"
            }`}
          >
            {label}
            {tab === key && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#1664FF]" />
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 max-w-[1440px] w-full mx-auto px-6 py-5 space-y-4">
        {/* Tab 1: Trace 看板 */}
        {tab === "traces" && (
          <div className="space-y-4">
            {traceData && (
              <div className="bg-white border border-[#E5E7EB] rounded-md grid grid-cols-2 lg:grid-cols-4 divide-y lg:divide-y-0 lg:divide-x divide-[#E5E7EB]">
                <div className="p-4">
                  <div className="text-[12px] text-[#6B7280]">总调用次数</div>
                  <div className="mt-1 text-[24px] font-semibold text-[#111827] tabular-nums tracking-tight">
                    {traceData.aggregate.total_calls}
                  </div>
                  <div className="mt-1 text-[11px] text-[#6B7280] tabular-nums">
                    非评测流量 {traceData.aggregate.user_calls ?? 0} · 评测跑批{" "}
                    {traceData.aggregate.eval_calls ?? 0}
                  </div>
                </div>
                <div className="p-4">
                  <div className="text-[12px] text-[#6B7280]">平均端到端延迟</div>
                  <div className="mt-1 text-[24px] font-semibold text-[#111827] tabular-nums tracking-tight">
                    {Math.round(traceData.aggregate.avg_latency_ms)}{" "}
                    <span className="text-[13px] font-normal text-[#6B7280]">ms</span>
                  </div>
                </div>
                <div className="p-4">
                  <div className="text-[12px] text-[#6B7280]">累计 Token 消耗</div>
                  <div className="mt-1 text-[24px] font-semibold text-[#111827] tabular-nums tracking-tight">
                    {traceData.aggregate.total_tokens.toLocaleString()}
                  </div>
                </div>
                <div className="p-4">
                  <div className="text-[12px] text-[#6B7280]">决策类型分布</div>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] font-mono tabular-nums text-[#374151]">
                    {Object.entries(traceData.aggregate.decision_distribution).map(
                      ([k, v]) => (
                        <span key={k}>
                          {k} <strong className="text-[#111827]">{v}</strong>
                        </span>
                      )
                    )}
                  </div>
                </div>
              </div>
            )}

            <div className="bg-white border border-[#E5E7EB] rounded-md overflow-hidden">
              <div className="px-4 py-3 border-b border-[#E5E7EB] flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                  <select
                    value={sourceFilter}
                    onChange={(e) => {
                      setSourceFilter(e.target.value as "all" | "user" | "eval");
                      setOffset(0);
                    }}
                    className="h-8 rounded border border-[#D1D5DB] px-2.5 bg-white text-[#111827]"
                  >
                    <option value="user">仅非评测流量（默认）</option>
                    <option value="all">全部来源</option>
                    <option value="eval">仅评测跑批 (eval-*)</option>
                  </select>
                  <select
                    value={intentFilter}
                    onChange={(e) => {
                      setIntentFilter(e.target.value);
                      setOffset(0);
                    }}
                    className="h-8 rounded border border-[#D1D5DB] px-2.5 bg-white text-[#111827]"
                  >
                    <option value="">全部意图</option>
                    <option value="kb_qa">kb_qa</option>
                    <option value="tool_ops">tool_ops</option>
                    <option value="escalate_hitl">escalate_hitl</option>
                    <option value="reject_unsafe">reject_unsafe</option>
                  </select>
                  <select
                    value={decisionFilter}
                    onChange={(e) => {
                      setDecisionFilter(e.target.value);
                      setOffset(0);
                    }}
                    className="h-8 rounded border border-[#D1D5DB] px-2.5 bg-white text-[#111827]"
                  >
                    <option value="">全部决策</option>
                    <option value="kb_answer">kb_answer</option>
                    <option value="kb_refuse">kb_refuse</option>
                    <option value="reject_unsafe">reject_unsafe</option>
                    <option value="tool_answer">tool_answer</option>
                    <option value="hitl_ticket_created">hitl_ticket_created</option>
                    <option value="hitl_takeover">hitl_takeover</option>
                  </select>
                </div>
                <div className="flex items-center gap-3">
                  {(traceData?.aggregate.eval_calls ?? 0) > 0 && (
                    <button
                      type="button"
                      disabled={cleanupBusy}
                      onClick={handleCleanupEvalTraces}
                      className="inline-flex items-center gap-1 text-[12px] text-[#6B7280] hover:text-red-600 disabled:opacity-50"
                      title="清理历史评测跑批产生的 eval-* 追踪记录，不影响真实客户会话"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      清理评测跑批 ({traceData?.aggregate.eval_calls})
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={loadTraces}
                    className="inline-flex items-center gap-1.5 text-[12px] text-[#6B7280] hover:text-[#111827]"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                    刷新
                  </button>
                </div>
              </div>

              {traceMsg && (
                <div className="px-4 py-2 bg-emerald-50 border-b border-emerald-100 text-[12.5px] text-emerald-700">
                  {traceMsg}
                </div>
              )}

              {traceError ? (
                <div className="py-10 text-center text-[13px] text-red-600">
                  {traceError}
                </div>
              ) : loadingInit && !traceData ? (
                <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                  正在加载 Trace 记录...
                </div>
              ) : !traceData || traceData.items.length === 0 ? (
                <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                  暂无 Trace 记录
                </div>
              ) : (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full text-[12.5px] text-left border-collapse">
                      <thead>
                        <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB] text-[11.5px] font-medium text-[#6B7280]">
                          <th className="py-2.5 px-4">ID</th>
                          <th className="py-2.5 px-3">意图</th>
                          <th className="py-2.5 px-3">最终决策</th>
                          <th className="py-2.5 px-3">检索最高分</th>
                          <th className="py-2.5 px-3">MCP 工具调用</th>
                          <th className="py-2.5 px-3 text-right">耗时</th>
                          <th className="py-2.5 px-3 text-right">Tokens (P/C)</th>
                          <th className="py-2.5 px-4 text-right">时间</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#F3F4F6] tabular-nums">
                        {traceData.items.map((t) => (
                          <tr key={t.id} className="hover:bg-[#F9FAFB]">
                            <td className="py-2.5 px-4 font-mono text-[#6B7280]">
                              #{t.id}
                            </td>
                            <td className="py-2.5 px-3 font-mono text-[#111827]">
                              {t.intent || "-"}
                            </td>
                            <td className="py-2.5 px-3">
                              <StatusBadge value={t.decision} subtle />
                            </td>
                            <td className="py-2.5 px-3 font-mono text-[#374151]">
                              {(t.rag_top_score ?? 0).toFixed(3)}
                            </td>
                            <td className="py-2.5 px-3 font-mono text-[12px] text-[#4B5563]">
                              {t.tool_calls.length > 0 ? t.tool_calls.join(", ") : "-"}
                            </td>
                            <td className="py-2.5 px-3 font-mono text-right text-[#111827]">
                              {t.latency_ms ?? 0} ms
                            </td>
                            <td className="py-2.5 px-3 font-mono text-right text-[#4B5563]">
                              {t.prompt_tokens ?? 0} / {t.completion_tokens ?? 0}
                            </td>
                            <td className="py-2.5 px-4 text-right text-[12px] text-[#9CA3AF]">
                              {formatLocalTime(t.created_at)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="px-4 py-2.5 border-t border-[#E5E7EB] bg-[#FBFBFC] flex items-center justify-between text-[12px] text-[#6B7280] tabular-nums">
                    <span>
                      共 {traceData.total} 条，当前 {offset + 1}–
                      {Math.min(offset + pageSize, traceData.total)}
                    </span>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - pageSize))}
                        className="px-2.5 py-1 rounded border border-[#D1D5DB] bg-white text-[#374151] disabled:opacity-40"
                      >
                        上一页
                      </button>
                      <button
                        type="button"
                        disabled={offset + pageSize >= traceData.total}
                        onClick={() => setOffset(offset + pageSize)}
                        className="px-2.5 py-1 rounded border border-[#D1D5DB] bg-white text-[#374151] disabled:opacity-40"
                      >
                        下一页
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* Tab 2: 32 题黄金集评测 */}
        {tab === "eval" && (
          <div className="space-y-4">
            <div className="bg-white border border-[#E5E7EB] rounded-md p-4 flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[220px]">
                <label className="block text-[12px] font-medium text-[#4B5563] mb-1">
                  评测批次名称
                </label>
                <input
                  type="text"
                  value={evalName}
                  onChange={(e) => setEvalName(e.target.value)}
                  className="w-full h-9 rounded border border-[#D1D5DB] px-3 text-[13px] focus:outline-none focus:border-[#1664FF]"
                />
              </div>
              <div className="w-40">
                <label className="block text-[12px] font-medium text-[#4B5563] mb-1">
                  题数上限（空为全量）
                </label>
                <input
                  type="number"
                  min={1}
                  max={32}
                  value={evalLimit}
                  onChange={(e) => setEvalLimit(e.target.value)}
                  placeholder="全部 32 题"
                  className="w-full h-9 rounded border border-[#D1D5DB] px-3 text-[13px] focus:outline-none focus:border-[#1664FF]"
                />
              </div>
              <button
                type="button"
                disabled={evalBusy}
                onClick={handleTriggerEval}
                className="h-9 inline-flex items-center gap-1.5 px-4 rounded bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[13px] font-medium disabled:opacity-50"
              >
                <Play className="w-3.5 h-3.5" />
                发起后台评测
              </button>
              <button
                type="button"
                onClick={loadEvalRuns}
                className="h-9 inline-flex items-center gap-1.5 px-3 rounded border border-[#D1D5DB] bg-white hover:bg-[#F9FAFB] text-[13px] text-[#374151]"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                刷新
              </button>
            </div>

            {evalErr && (
              <div className="rounded-md bg-red-50 border border-red-200 px-4 py-2.5 text-[12.5px] text-red-700">
                {evalErr}
              </div>
            )}
            {evalMsg && (
              <div className="rounded-md bg-[#EFF4FF] border border-[#BFDBFE] px-4 py-2.5 text-[12.5px] text-[#1E40AF]">
                {evalMsg}
              </div>
            )}

            <div className="bg-white border border-[#E5E7EB] rounded-md overflow-hidden">
              <div className="px-4 py-3 border-b border-[#E5E7EB] flex flex-wrap items-center justify-between gap-2">
                <span className="text-[13px] font-semibold text-[#111827]">
                  历史评测对比（点击行展开逐题判定）
                </span>
                <span className="text-[11.5px] text-[#6B7280]">
                  口径说明：拒答拆分为「严格拒答 / 零幻觉路由」，工具拆分为「MCP 诊断 / HITL 升级」，引用命中率为启发式校验（非 LLM Judge）
                </span>
              </div>
              {loadingInit && evalRuns.length === 0 ? (
                <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                  正在加载评测记录...
                </div>
              ) : evalRuns.length === 0 ? (
                <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                  暂无评测记录
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12.5px] text-left border-collapse">
                    <thead>
                      <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB] text-[11.5px] font-medium text-[#6B7280]">
                        <th className="py-2.5 px-4">ID</th>
                        <th className="py-2.5 px-3">批次名称</th>
                        <th className="py-2.5 px-3">状态</th>
                        <th className="py-2.5 px-3 text-right">题数 / 异常</th>
                        <th className="py-2.5 px-3 text-right">意图准确</th>
                        <th className="py-2.5 px-3 text-right">严格拒答 / 零幻觉</th>
                        <th className="py-2.5 px-3 text-right">安全拦截</th>
                        <th className="py-2.5 px-3 text-right">MCP / HITL</th>
                        <th className="py-2.5 px-3 text-right">引用命中(启发)</th>
                        <th className="py-2.5 px-3 text-right">平均延迟</th>
                        <th className="py-2.5 px-4 text-right">运行时间</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#F3F4F6] tabular-nums">
                      {evalRuns.map((run) => {
                        const active = selectedRun?.id === run.id;
                        const isRunning = run.status === "running";
                        const isFailed = run.status === "failed";
                        const hasNewCite = run.citation_hit_rate != null;
                        const citeRate = run.citation_hit_rate ?? run.avg_faithfulness;
                        return (
                          <tr
                            key={run.id}
                            onClick={async () => {
                              try {
                                setSelectedRun(await api.getEvalRun(run.id));
                              } catch (e: any) {
                                setEvalErr(e?.message || "加载评测详情失败");
                              }
                            }}
                            className={`cursor-pointer transition-colors ${
                              active ? "bg-[#EFF4FF]/70" : "hover:bg-[#F9FAFB]"
                            }`}
                          >
                            <td className="py-2.5 px-4 font-mono text-[#6B7280]">
                              #{run.id}
                            </td>
                            <td className="py-2.5 px-3 font-medium text-[#111827]">
                              {run.run_name}
                            </td>
                            <td className="py-2.5 px-3">
                              <span
                                className={`inline-flex items-center gap-1.5 text-[11.5px] font-medium ${
                                  isRunning
                                    ? "text-[#1664FF]"
                                    : isFailed
                                    ? "text-[#DC2626]"
                                    : "text-[#059669]"
                                }`}
                              >
                                <span
                                  className={`w-1.5 h-1.5 rounded-full ${
                                    isRunning
                                      ? "bg-[#1664FF] animate-pulse"
                                      : isFailed
                                      ? "bg-red-500"
                                      : "bg-emerald-500"
                                  }`}
                                />
                                {isRunning ? "运行中" : isFailed ? "失败" : "已完成"}
                              </span>
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono">
                              {run.total_cases}
                              {run.error_cases ? (
                                <span className="text-red-600 ml-1">
                                  / {run.error_cases}错
                                </span>
                              ) : (
                                <span className="text-[#9CA3AF] ml-1">/ 0</span>
                              )}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono font-medium text-[#111827]">
                              {pct(run.intent_acc)}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono text-[#374151]">
                              {pct(run.refusal_acc)}
                              <span className="text-[#9CA3AF] mx-0.5">/</span>
                              <span className="text-[#059669] font-medium">
                                {pct(run.safe_routing_rate)}
                              </span>
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono font-medium text-[#059669]">
                              {pct(run.security_block_rate)}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono font-medium text-[#1664FF]">
                              {run.tool_ops_acc != null || run.hitl_escalate_acc != null ? (
                                <>
                                  {pct(run.tool_ops_acc)}
                                  <span className="text-[#9CA3AF] mx-0.5">/</span>
                                  {pct(run.hitl_escalate_acc)}
                                </>
                              ) : (
                                pct(run.tool_acc)
                              )}
                            </td>
                            <td
                              className="py-2.5 px-3 text-right font-mono text-[#374151]"
                              title={
                                hasNewCite
                                  ? "citation_hit_rate（启发式校验）"
                                  : "历史批次兼容回退自 avg_faithfulness 列"
                              }
                            >
                              {pct(citeRate)}
                              {!hasNewCite && citeRate != null && (
                                <span className="ml-1 text-[10.5px] text-[#9CA3AF]">
                                  旧
                                </span>
                              )}
                            </td>
                            <td className="py-2.5 px-3 text-right font-mono text-[#4B5563]">
                              {run.avg_latency_ms != null
                                ? `${Math.round(run.avg_latency_ms)} ms`
                                : "-"}
                            </td>
                            <td className="py-2.5 px-4 text-right text-[12px] text-[#9CA3AF]">
                              {formatLocalTime(run.created_at)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {selectedRun && (
              <div className="bg-white border border-[#E5E7EB] rounded-md overflow-hidden">
                <div className="px-4 py-3 bg-[#F9FAFB] border-b border-[#E5E7EB] flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2.5">
                    <span className="text-[13px] font-semibold text-[#111827]">
                      跑批 #{selectedRun.id}「{selectedRun.run_name}」逐题明细（共{" "}
                      {selectedRun.cases.length} 题）
                    </span>
                    <span className="inline-flex items-center px-2 py-0.5 rounded bg-[#F3F4F6] text-[11.5px] font-mono text-[#4B5563]">
                      judge_mode: {selectedRun.judge_mode || "citation_heuristic"}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSelectedRun(null)}
                    className="text-[12px] text-[#6B7280] hover:text-[#111827]"
                  >
                    收起
                  </button>
                </div>
                <div className="overflow-x-auto max-h-[380px]">
                  <table className="w-full text-[12.5px] text-left border-collapse">
                    <thead>
                      <tr className="bg-[#FBFBFC] border-b border-[#E5E7EB] text-[11.5px] font-medium text-[#6B7280]">
                        <th className="py-2 px-4">题号</th>
                        <th className="py-2 px-3">分类</th>
                        <th className="py-2 px-3">判定</th>
                        <th className="py-2 px-3">实际意图 / 决策</th>
                        <th className="py-2 px-4">说明</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#F3F4F6]">
                      {selectedRun.cases.map((c) => (
                        <tr key={c.id} className="hover:bg-[#F9FAFB]">
                          <td className="py-2 px-4 font-mono text-[#111827]">
                            {c.case_id}
                          </td>
                          <td className="py-2 px-3 text-[#4B5563]">{c.category}</td>
                          <td className="py-2 px-3">
                            <span
                              className={`inline-flex items-center gap-1.5 font-mono text-[11.5px] font-semibold ${
                                c.passed ? "text-[#059669]" : "text-[#DC2626]"
                              }`}
                            >
                              <span
                                className={`w-1.5 h-1.5 rounded-full ${
                                  c.passed ? "bg-emerald-500" : "bg-red-500"
                                }`}
                              />
                              {c.passed ? "PASS" : "FAIL"}
                            </span>
                          </td>
                          <td className="py-2 px-3 font-mono text-[12px] text-[#374151]">
                            {c.actual?.intent} / {c.actual?.decision}
                          </td>
                          <td className="py-2 px-4 text-[#4B5563]">
                            {c.reason || "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Tab 3: 知识库检索预览 */}
        {tab === "kb" && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
            <div className="lg:col-span-7 bg-white border border-[#E5E7EB] rounded-md p-4 space-y-4">
              <div>
                <h3 className="text-[14px] font-semibold text-[#111827]">
                  检索打分与拒答阈值调参
                </h3>
                <p className="text-[12px] text-[#6B7280] mt-0.5">
                  调整 RAG_MIN_SCORE 阈值，实时观察 jieba 分词、BM25 原始分、IDF 覆盖率与放行边界。
                </p>
              </div>

              <form onSubmit={handleRunSearchPreview} className="space-y-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={kbQuery}
                    onChange={(e) => setKbQuery(e.target.value)}
                    placeholder="输入测试查询词，如：Nginx 502 怎么排查"
                    className="flex-1 h-9 rounded border border-[#D1D5DB] px-3 text-[13px] focus:outline-none focus:border-[#1664FF]"
                  />
                  <button
                    type="submit"
                    className="h-9 inline-flex items-center gap-1.5 px-4 rounded bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[13px] font-medium"
                  >
                    <Search className="w-3.5 h-3.5" />
                    测试检索
                  </button>
                </div>

                <div className="flex flex-wrap items-center gap-6 text-[12.5px] text-[#4B5563] bg-[#F9FAFB] border border-[#EAECEF] rounded px-3 py-2">
                  <label className="flex items-center gap-2.5">
                    <span>拒答阈值</span>
                    <input
                      type="range"
                      min={0.15}
                      max={0.65}
                      step={0.05}
                      value={kbMinScore}
                      onChange={(e) => setKbMinScore(Number(e.target.value))}
                      className="accent-[#1664FF]"
                    />
                    <span className="font-mono font-semibold text-[#111827] tabular-nums w-10">
                      {kbMinScore.toFixed(2)}
                    </span>
                  </label>
                  <label className="flex items-center gap-2">
                    <span>返回条数 Top K</span>
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={kbTopK}
                      onChange={(e) => setKbTopK(Number(e.target.value))}
                      className="w-16 h-7 rounded border border-[#D1D5DB] bg-white px-2 text-[12.5px] font-mono"
                    />
                  </label>
                </div>
              </form>

              {kbErr && (
                <div className="rounded bg-red-50 border border-red-200 p-2.5 text-[12.5px] text-red-700">
                  {kbErr}
                </div>
              )}
              {kbMsg && (
                <div className="rounded bg-[#EFF4FF] border border-[#BFDBFE] p-2.5 text-[12.5px] text-[#1E40AF]">
                  {kbMsg}
                </div>
              )}

              {kbResult && (
                <div className="space-y-3 pt-2 border-t border-[#EAECEF]">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px]">
                    <div className="flex flex-wrap items-center gap-3 text-[#4B5563]">
                      <span>
                        分词：
                        <code className="bg-[#F3F4F6] px-1.5 py-0.5 rounded text-[#111827] text-[12px]">
                          {kbResult.tokens.join(" / ") || "无"}
                        </code>
                      </span>
                      <span className="tabular-nums">
                        最高分{" "}
                        <strong className="font-mono text-[#111827]">
                          {kbResult.top_score.toFixed(4)}
                        </strong>{" "}
                        / 阈值{" "}
                        <span className="font-mono">{kbResult.threshold.toFixed(2)}</span>
                      </span>
                    </div>
                    <span
                      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded text-[12px] font-medium ${
                        kbResult.accepted
                          ? "bg-emerald-50 text-[#065F46]"
                          : "bg-amber-50 text-[#92400E]"
                      }`}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${
                          kbResult.accepted ? "bg-emerald-500" : "bg-amber-500"
                        }`}
                      />
                      {kbResult.accepted ? "放行（进入有据回答）" : "拦截（触发严格拒答）"}
                    </span>
                  </div>

                  <div className="divide-y divide-[#EAECEF] border border-[#E5E7EB] rounded-md overflow-hidden">
                    {kbResult.hits.map((h) => (
                      <div key={h.chunk_id} className="p-3.5 bg-white space-y-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[13px] font-semibold text-[#111827]">
                            {h.citation}
                          </span>
                          <span className="font-mono text-[11.5px] text-[#6B7280] tabular-nums">
                            score {h.score.toFixed(3)} · bm25 {h.bm25_raw.toFixed(2)} · cov{" "}
                            {h.coverage.toFixed(2)}
                          </span>
                        </div>
                        <div className="h-1 w-full bg-[#F3F4F6] rounded overflow-hidden">
                          <div
                            className="h-full bg-[#1664FF]"
                            style={{ width: `${Math.min(100, Math.round(h.score * 100))}%` }}
                          />
                        </div>
                        <p className="text-[12.5px] text-[#4B5563] leading-relaxed">
                          {h.content}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="lg:col-span-5 bg-white border border-[#E5E7EB] rounded-md overflow-hidden flex flex-col">
              <div className="px-4 py-3 bg-[#F9FAFB] border-b border-[#E5E7EB] flex items-center justify-between">
                <span className="text-[13px] font-semibold text-[#111827] tabular-nums">
                  已索引文档（{kbDocs.length} 篇）
                </span>
                <button
                  type="button"
                  onClick={handleReindex}
                  className="inline-flex items-center gap-1 text-[12px] text-[#4B5563] hover:text-[#111827]"
                >
                  <RefreshCw className="w-3 h-3" />
                  重建索引
                </button>
              </div>
              <div className="divide-y divide-[#F3F4F6] max-h-[520px] overflow-y-auto">
                {loadingInit && kbDocs.length === 0 ? (
                  <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                    正在加载知识库文档...
                  </div>
                ) : kbDocs.length === 0 ? (
                  <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                    暂无已索引文档
                  </div>
                ) : (
                  kbDocs.map((d) => (
                    <div
                      key={d.id}
                      className="px-4 py-2.5 flex items-center justify-between gap-3 text-[12.5px] hover:bg-[#F9FAFB]"
                    >
                      <div className="min-w-0">
                        <div className="font-medium text-[#111827] truncate">{d.title}</div>
                        <div className="text-[11.5px] text-[#9CA3AF]">
                          {d.category || "通用"} · v{d.version}
                        </div>
                      </div>
                      <span className="font-mono text-[11.5px] text-[#6B7280] tabular-nums shrink-0">
                        {d.chunk_count} 切片
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        {/* Tab 4: 仿真实例故障靶场 */}
        {tab === "faults" && (
          <div className="space-y-4">
            {faultErr && (
              <div className="rounded-md bg-red-50 border border-red-200 px-4 py-2.5 text-[12.5px] text-red-700">
                {faultErr}
              </div>
            )}
            {faultMsg && (
              <div className="rounded-md bg-emerald-50 border border-emerald-200 px-4 py-2.5 text-[12.5px] text-[#065F46]">
                {faultMsg}
              </div>
            )}
            {loadingInit && instances.length === 0 ? (
              <div className="py-10 text-center text-[13px] text-[#9CA3AF]">
                正在加载仿真实例列表...
              </div>
            ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {instances.map((inst) => (
                <InstanceCard
                  key={inst.id}
                  instance={inst}
                  actionSlot={
                    <div className="w-full flex items-center justify-between gap-2">
                      <span className="text-[11.5px] text-[#6B7280]">
                        归属：{inst.owner_name || `#${inst.owner_id}`}
                      </span>
                      <select
                        defaultValue=""
                        onChange={(e) => {
                          if (e.target.value) {
                            handleApplyPreset(inst.id, e.target.value);
                            e.target.value = "";
                          }
                        }}
                        className="h-7 rounded border border-[#D1D5DB] bg-white px-2 text-[12px] text-[#111827]"
                      >
                        <option value="" disabled>
                          切换故障预设...
                        </option>
                        {PRESET_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  }
                />
              ))}
            </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
