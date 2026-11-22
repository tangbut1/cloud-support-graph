import React, { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { api, ApiError, formatLocalTime, TicketItem } from "../api/client";
import { StatusBadge } from "./StatusBadge";

const ALL_STATUSES: TicketItem["status"][] = [
  "OPEN",
  "TRIAGED",
  "IN_PROGRESS",
  "RESOLVED",
  "CLOSED",
];

interface Props {
  tickets: TicketItem[];
  canManage?: boolean;
  onRefresh: () => void;
}

export const TicketTable: React.FC<Props> = ({
  tickets,
  canManage = false,
  onRefresh,
}) => {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<TicketItem | null>(null);
  const [nextAllowed, setNextAllowed] = useState<string[]>([]);
  const [targetStatus, setTargetStatus] = useState<string>("TRIAGED");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const loadDetail = async (id: number) => {
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const [d, next] = await Promise.all([
        api.getTicket(id),
        api.getNextTicketStatuses(id),
      ]);
      setDetail(d);
      setNextAllowed(next);
      setTargetStatus(next[0] || "CLOSED");
    } catch (e: any) {
      setErrorMsg(e?.message || "加载工单详情失败");
    }
  };

  useEffect(() => {
    if (selectedId != null) {
      loadDetail(selectedId);
    } else if (tickets.length > 0) {
      setSelectedId(tickets[0].id);
    } else {
      setDetail(null);
    }
  }, [selectedId, tickets.length]);

  const handleTransition = async (toStatus: string) => {
    if (!detail) return;
    setBusy(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const updated = await api.transitionTicket(
        detail.id,
        toStatus,
        comment.trim() || `状态推进至 ${toStatus}`
      );
      setDetail(updated);
      const next = await api.getNextTicketStatuses(detail.id);
      setNextAllowed(next);
      setTargetStatus(next[0] || "CLOSED");
      setComment("");
      setSuccessMsg(`工单 ${updated.ticket_no} 已流转至 ${updated.status}`);
      onRefresh();
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        setErrorMsg(`非法跃迁已被状态机拦截（409）：${e.message}`);
      } else {
        setErrorMsg(e?.message || "状态流转失败");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 border border-[#E5E7EB] rounded-md bg-white overflow-hidden">
      {/* 左表 */}
      <div className="lg:col-span-7 border-b lg:border-b-0 lg:border-r border-[#E5E7EB] flex flex-col">
        <div className="px-4 py-2.5 bg-[#F9FAFB] border-b border-[#E5E7EB] flex items-center justify-between">
          <span className="text-[12.5px] font-semibold text-[#111827] tabular-nums">
            工单列表（{tickets.length}）
          </span>
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex items-center gap-1 text-[12px] text-[#6B7280] hover:text-[#111827]"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            刷新
          </button>
        </div>

        {tickets.length === 0 ? (
          <div className="p-10 text-center text-[13px] text-[#9CA3AF]">
            暂无工单记录
          </div>
        ) : (
          <div className="divide-y divide-[#F3F4F6] max-h-[540px] overflow-y-auto">
            {tickets.map((tk) => {
              const active = selectedId === tk.id;
              return (
                <button
                  key={tk.id}
                  type="button"
                  onClick={() => setSelectedId(tk.id)}
                  className={`w-full text-left px-4 py-3 transition-colors relative ${
                    active ? "bg-[#EFF4FF]/60" : "hover:bg-[#F9FAFB]"
                  }`}
                >
                  {active && (
                    <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-[#1664FF]" />
                  )}
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="font-mono text-[12px] font-semibold text-[#111827] tabular-nums">
                        {tk.ticket_no}
                      </span>
                      <span className="text-[11px] font-mono text-[#6B7280] bg-[#F3F4F6] px-1.5 py-0.5 rounded">
                        {tk.priority}
                      </span>
                    </div>
                    <StatusBadge value={tk.status} subtle />
                  </div>
                  <div className="mt-1 text-[13px] text-[#1F2937] font-medium truncate">
                    {tk.title}
                  </div>
                  <div className="mt-1 text-[11.5px] text-[#9CA3AF] tabular-nums">
                    创建于 {formatLocalTime(tk.created_at)}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 右详情 + 审计轴 */}
      <div className="lg:col-span-5 p-4 bg-[#FBFBFC] space-y-4">
        {!detail ? (
          <div className="text-[13px] text-[#9CA3AF] py-12 text-center">
            选择左侧工单查看详情与状态审计轴
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[13px] font-semibold text-[#111827] tabular-nums">
                  {detail.ticket_no}
                </span>
                <StatusBadge value={detail.status} />
              </div>
              <h3 className="text-[14px] font-semibold text-[#111827] leading-snug">
                {detail.title}
              </h3>
              {detail.description && (
                <p className="text-[12.5px] text-[#4B5563] bg-white border border-[#E5E7EB] rounded p-2.5 leading-relaxed">
                  {detail.description}
                </p>
              )}
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[#6B7280]">
                <span>优先级 {detail.priority}</span>
                {detail.category && <span>分类 {detail.category}</span>}
                <span>
                  允许流转：
                  <span className="font-mono text-[#111827]">
                    {nextAllowed.length > 0 ? nextAllowed.join(" / ") : "无（终态）"}
                  </span>
                </span>
              </div>
            </div>

            {canManage && (
              <div className="border border-[#E5E7EB] bg-white rounded-md p-3 space-y-2.5">
                <div className="text-[12px] font-semibold text-[#111827]">
                  推进工单状态
                </div>
                <div className="flex gap-2">
                  <select
                    value={targetStatus}
                    onChange={(e) => setTargetStatus(e.target.value)}
                    className="flex-1 rounded border border-[#D1D5DB] bg-white px-2.5 py-1.5 text-[12.5px] focus:outline-none focus:border-[#1664FF]"
                  >
                    {ALL_STATUSES.map((st) => (
                      <option key={st} value={st}>
                        {st}
                        {nextAllowed.includes(st) ? "（合法）" : "（非法跃迁验证）"}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleTransition(targetStatus)}
                    className="px-3 py-1.5 rounded bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[12.5px] font-medium disabled:opacity-50"
                  >
                    执行流转
                  </button>
                </div>
                <input
                  type="text"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="操作备注（写入不可变审计日志）"
                  className="w-full rounded border border-[#D1D5DB] bg-white px-2.5 py-1.5 text-[12.5px] focus:outline-none focus:border-[#1664FF]"
                />
                {errorMsg && (
                  <div className="flex items-start gap-1.5 rounded bg-red-50 border border-red-200 p-2 text-[12px] text-red-700">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>{errorMsg}</span>
                  </div>
                )}
                {successMsg && (
                  <div className="rounded bg-emerald-50 border border-emerald-200 p-2 text-[12px] text-emerald-800">
                    {successMsg}
                  </div>
                )}
              </div>
            )}

            <div>
              <div className="text-[12px] font-semibold text-[#111827] mb-2.5">
                状态审计时间轴
              </div>
              <ol className="relative border-l border-[#E5E7EB] ml-1.5 space-y-3">
                {(detail.events || []).map((ev) => (
                  <li key={ev.id} className="ml-4 text-[12px]">
                    <span className="absolute -left-[4.5px] mt-1.5 w-2 h-2 rounded-full bg-[#1664FF] ring-2 ring-[#FBFBFC]" />
                    <div className="font-mono font-medium text-[#111827]">
                      {ev.from_status
                        ? `${ev.from_status} → ${ev.to_status}`
                        : `创建 → ${ev.to_status}`}
                    </div>
                    {ev.comment && (
                      <div className="text-[#4B5563] mt-0.5">{ev.comment}</div>
                    )}
                    <div className="text-[11px] text-[#9CA3AF] mt-0.5 tabular-nums">
                      {formatLocalTime(ev.created_at)}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
