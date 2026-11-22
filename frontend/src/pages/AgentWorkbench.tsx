import React, { useCallback, useEffect, useState } from "react";
import { RefreshCw, Send } from "lucide-react";
import {
  AgentQueueItem,
  api,
  ConversationItem,
  formatLocalTime,
  MessageItem,
  TicketItem,
} from "../api/client";
import { AuthImage } from "../components/AuthImage";
import { HandoffCard } from "../components/HandoffCard";
import { Navbar } from "../components/Navbar";
import { StatusBadge } from "../components/StatusBadge";
import { TicketTable } from "../components/TicketTable";

export const AgentWorkbench: React.FC = () => {
  const [tab, setTab] = useState<"queue" | "tickets">("queue");
  const [queue, setQueue] = useState<AgentQueueItem[]>([]);
  const [handlingList, setHandlingList] = useState<ConversationItem[]>([]);
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [selectedConv, setSelectedConv] = useState<ConversationItem | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [tickets, setTickets] = useState<TicketItem[]>([]);

  const [comment, setComment] = useState("");
  const [replyText, setReplyText] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingInit, setLoadingInit] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [banner, setBanner] = useState<{ type: "ok" | "err"; text: string } | null>(
    null
  );

  const loadAll = useCallback(async () => {
    try {
      const [q, h, t] = await Promise.all([
        api.getAgentQueue(),
        api.listConversations("human_handling"),
        api.listTickets(),
      ]);
      setQueue(q);
      setHandlingList(h);
      setTickets(t);
      return { q, h };
    } catch (e: any) {
      setBanner({ type: "err", text: e?.message || "加载坐席数据失败" });
      return { q: [], h: [] };
    }
  }, []);

  const loadConversationDetail = useCallback(async (id: string, showSpinner = false) => {
    if (showSpinner) setLoadingDetail(true);
    try {
      const [conv, msgs] = await Promise.all([
        api.getConversation(id),
        api.listMessages(id),
      ]);
      setSelectedConv(conv);
      setMessages(msgs);
    } catch (e: any) {
      setBanner({ type: "err", text: e?.message || "加载会话详情失败" });
    } finally {
      if (showSpinner) setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    setLoadingInit(true);
    loadAll()
      .then(({ q, h }) => {
        if (!active) return;
        if (q.length > 0) setSelectedConvId(q[0].conversation_id);
        else if (h.length > 0) setSelectedConvId(h[0].id);
      })
      .finally(() => {
        if (active) setLoadingInit(false);
      });
    return () => {
      active = false;
    };
  }, [loadAll]);

  useEffect(() => {
    if (selectedConvId) {
      // 切换会话立即清空旧会话详情与草稿，防止网络返回前出现"展示 A、操作 B"的错位窗口
      setSelectedConv(null);
      setMessages([]);
      setComment("");
      setReplyText("");
      loadConversationDetail(selectedConvId, true);
    } else {
      setSelectedConv(null);
      setMessages([]);
      setComment("");
      setReplyText("");
    }
  }, [selectedConvId, loadConversationDetail]);

  // 自动轮询待接管队列与当前会话消息，避免坐席手动刷新遗漏新进线
  useEffect(() => {
    const timer = window.setInterval(() => {
      loadAll();
      if (selectedConvId) {
        loadConversationDetail(selectedConvId);
      }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [loadAll, selectedConvId, loadConversationDetail]);

  const handleResume = async (
    action: "approve_ticket_and_reply" | "takeover_only"
  ) => {
    if (!selectedConvId) return;
    setBusy(true);
    setBanner(null);
    try {
      const res = await api.resumeConversation(
        selectedConvId,
        action,
        comment.trim() || undefined
      );
      setComment("");
      setBanner({
        type: "ok",
        text: res.ticket_no
          ? `断点恢复成功：已原子创建工单 ${res.ticket_no}，会话转入人工跟进。`
          : res.forced_takeover
          ? "原审批检查点已失效，已强制接管会话转入人工跟进。"
          : `已接管会话（未建单），当前状态：${res.conversation_status}。`,
      });
      await Promise.all([loadAll(), loadConversationDetail(selectedConvId)]);
    } catch (e: any) {
      setBanner({ type: "err", text: e?.message || "审批恢复失败" });
    } finally {
      setBusy(false);
    }
  };

  const handleReply = async (closeAfter: boolean) => {
    if (!selectedConvId || !replyText.trim()) return;
    setBusy(true);
    setBanner(null);
    try {
      const res = await api.replyConversation(
        selectedConvId,
        replyText.trim(),
        closeAfter
      );
      setReplyText("");
      setBanner({
        type: "ok",
        text: closeAfter
          ? "已回复客户并关闭本次会话。"
          : `回复已发送（当前状态：${res.conversation_status}）。`,
      });
      await Promise.all([loadAll(), loadConversationDetail(selectedConvId)]);
    } catch (e: any) {
      setBanner({ type: "err", text: e?.message || "发送回复失败" });
    } finally {
      setBusy(false);
    }
  };

  const selectedQueueItem = queue.find(
    (item) => item.conversation_id === selectedConvId
  );

  return (
    <div className="h-screen flex flex-col bg-white overflow-hidden">
      <Navbar />

      {/* 二级工作台栏 */}
      <div className="h-11 px-5 bg-white border-b border-[#E5E7EB] flex items-center justify-between shrink-0">
        <div className="flex items-center h-full gap-6">
          <button
            type="button"
            onClick={() => setTab("queue")}
            className={`relative h-full text-[13px] tabular-nums transition-colors ${
              tab === "queue"
                ? "text-[#111827] font-semibold"
                : "text-[#6B7280] hover:text-[#111827]"
            }`}
          >
            待接管与人工对话（待审批 {queue.length} · 处理中 {handlingList.length}）
            {tab === "queue" && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#1664FF]" />
            )}
          </button>
          <button
            type="button"
            onClick={() => setTab("tickets")}
            className={`relative h-full text-[13px] tabular-nums transition-colors ${
              tab === "tickets"
                ? "text-[#111827] font-semibold"
                : "text-[#6B7280] hover:text-[#111827]"
            }`}
          >
            工单状态机管理（{tickets.length}）
            {tab === "tickets" && (
              <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#1664FF]" />
            )}
          </button>
        </div>

        <button
          type="button"
          onClick={() => {
            loadAll();
            if (selectedConvId) loadConversationDetail(selectedConvId);
          }}
          className="inline-flex items-center gap-1.5 text-[12px] text-[#6B7280] hover:text-[#111827]"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          刷新队列
        </button>
      </div>

      {banner && (
        <div
          data-testid="agent-banner"
          className={`px-5 py-2 text-[12.5px] border-b shrink-0 ${
            banner.type === "ok"
              ? "bg-emerald-50 border-emerald-200 text-emerald-800"
              : "bg-red-50 border-red-200 text-red-700"
          }`}
        >
          {banner.text}
        </div>
      )}

      {tab === "tickets" ? (
        <div className="flex-1 overflow-y-auto p-5 bg-[#F7F8FA]">
          <TicketTable tickets={tickets} canManage onRefresh={loadAll} />
        </div>
      ) : (
        /* 三栏专业客服台：左队列 / 中会话 / 右 AI 诊断与审批面板 */
        <div className="flex-1 flex min-h-0">
          {/* 左栏：收件箱队列 */}
          <aside className="w-[300px] shrink-0 bg-[#FBFBFC] border-r border-[#E5E7EB] flex flex-col overflow-y-auto">
            <div className="px-3.5 py-2.5 border-b border-[#EAECEF] flex items-center justify-between">
              <span className="text-[12px] font-semibold text-[#111827]">
                待接管审批
              </span>
              <span className="text-[11.5px] font-mono tabular-nums text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded">
                {queue.length}
              </span>
            </div>

            {loadingInit && queue.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[#9CA3AF] border-b border-[#EAECEF]">
                正在加载待审批队列...
              </div>
            ) : queue.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[#9CA3AF] border-b border-[#EAECEF]">
                当前无挂起等待审批的会话
              </div>
            ) : (
              <div className="divide-y divide-[#EAECEF] border-b border-[#EAECEF]">
                {queue.map((item) => {
                  const active = selectedConvId === item.conversation_id;
                  return (
                    <button
                      key={item.conversation_id}
                      type="button"
                      data-testid={`queue-item-${item.conversation_id}`}
                      onClick={() => setSelectedConvId(item.conversation_id)}
                      className={`w-full text-left px-3.5 py-3 transition-colors relative ${
                        active ? "bg-[#EFF4FF]/80" : "hover:bg-[#F3F4F6]/60"
                      }`}
                    >
                      {active && (
                        <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-[#1664FF]" />
                      )}
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[13px] font-semibold text-[#111827]">
                          {item.customer_name}
                        </span>
                        <StatusBadge value="pending_human" subtle />
                      </div>
                      <div className="mt-1 text-[12px] text-[#4B5563] line-clamp-2 leading-snug">
                        {item.handoff_summary?.diagnosis ||
                          item.handoff_summary?.customer_intent ||
                          "等待人工审批"}
                      </div>
                      <div className="mt-1.5 text-[11px] text-[#9CA3AF] tabular-nums">
                        {formatLocalTime(item.updated_at)}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            <div className="px-3.5 py-2.5 border-b border-[#EAECEF] flex items-center justify-between">
              <span className="text-[12px] font-semibold text-[#111827]">
                人工处理中
              </span>
              <span className="text-[11.5px] font-mono tabular-nums text-[#6B7280]">
                {handlingList.length}
              </span>
            </div>

            {loadingInit && handlingList.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[#9CA3AF]">
                正在加载进行中会话...
              </div>
            ) : handlingList.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[#9CA3AF]">
                暂无进行中的人工对话
              </div>
            ) : (
              <div className="divide-y divide-[#F3F4F6]">
                {handlingList.map((c) => {
                  const active = selectedConvId === c.id;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setSelectedConvId(c.id)}
                      className={`w-full text-left px-3.5 py-2.5 transition-colors relative ${
                        active ? "bg-[#EFF4FF]/80" : "hover:bg-[#F3F4F6]/60"
                      }`}
                    >
                      {active && (
                        <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-[#1664FF]" />
                      )}
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[13px] font-medium text-[#111827]">
                          {c.customer_name || `客户 #${c.customer_id}`}
                        </span>
                        <StatusBadge value={c.status} subtle />
                      </div>
                      <div className="mt-1 text-[12px] text-[#6B7280] truncate">
                        {c.last_message || c.id.slice(0, 8)}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </aside>

          {/* 中栏：客户对话记录 + 坐席回复框 */}
          <main className="flex-1 flex flex-col min-w-0 bg-white">
            {(loadingInit || loadingDetail) && !selectedConv ? (
              <div
                data-testid="agent-loading"
                className="flex-1 flex items-center justify-center text-[13px] text-[#9CA3AF]"
              >
                正在加载会话详情...
              </div>
            ) : !selectedConv ? (
              <div className="flex-1 flex items-center justify-center text-[13px] text-[#9CA3AF]">
                请从左侧选择待审批或处理中的会话
              </div>
            ) : (
              <>
                <div className="px-5 py-3 border-b border-[#E5E7EB] flex items-center justify-between shrink-0">
                  <div className="flex items-center gap-2.5">
                    <span className="text-[14px] font-semibold text-[#111827]">
                      {selectedConv.customer_name ||
                        `客户 #${selectedConv.customer_id}`}
                    </span>
                    <StatusBadge value={selectedConv.status} />
                  </div>
                  <span className="text-[11.5px] font-mono text-[#9CA3AF]">
                    会话 {selectedConv.id.slice(0, 8)} · 线程{" "}
                    {selectedConv.thread_id.slice(0, 8)}
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
                  {loadingDetail && messages.length === 0 ? (
                    <div
                      data-testid="agent-messages-loading"
                      className="py-10 text-center text-[13px] text-[#9CA3AF]"
                    >
                      正在加载会话消息...
                    </div>
                  ) : (
                    messages.map((m) => {
                    const isCustomer = m.sender_role === "customer";
                    const isAgent = m.sender_role === "agent";
                    return (
                      <div
                        key={m.id}
                        className={`rounded-md px-3.5 py-2.5 text-[13px] border ${
                          isCustomer
                            ? "bg-[#F9FAFB] border-[#E5E7EB]"
                            : isAgent
                            ? "bg-[#ECFDF5]/60 border-emerald-200"
                            : "bg-white border-[#E5E7EB] border-l-2 border-l-[#1664FF]"
                        }`}
                      >
                        <div className="flex items-center justify-between text-[11.5px] text-[#6B7280] mb-1 tabular-nums">
                          <span className="font-semibold text-[#111827]">
                            {isCustomer
                              ? selectedConv.customer_name || "客户"
                              : isAgent
                              ? "人工坐席"
                              : "AI 助手"}
                          </span>
                          <span>{formatLocalTime(m.created_at)}</span>
                        </div>
                        <div className="whitespace-pre-wrap text-[#1F2937] leading-relaxed">
                          {m.content}
                        </div>
                        {m.image_url && <AuthImage src={m.image_url} />}
                      </div>
                    );
                  })
                  )}
                </div>

                {selectedConv.status === "pending_human" && (
                  <div className="border-t border-[#E5E7EB] p-4 bg-[#FBFBFC] shrink-0 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[12.5px] font-semibold text-[#111827]">
                        人工审批与断点恢复
                      </span>
                      <span className="text-[11.5px] text-[#6B7280]">
                        批准后从检查点恢复并原子创建工单
                      </span>
                    </div>
                    <input
                      type="text"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="填写坐席审批备注（可选，会随工单回复发送给客户）"
                      className="w-full h-9 rounded-md border border-[#D1D5DB] bg-white px-3 text-[13px] focus:outline-none focus:border-[#1664FF]"
                    />
                    <div className="flex items-center gap-2.5">
                      <button
                        type="button"
                        data-testid="approve-ticket-btn"
                        disabled={busy}
                        onClick={() => handleResume("approve_ticket_and_reply")}
                        className="px-4 py-2 rounded-md bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[13px] font-medium transition-colors disabled:opacity-50"
                      >
                        批准建单并回复客户
                      </button>
                      <button
                        type="button"
                        data-testid="takeover-only-btn"
                        disabled={busy}
                        onClick={() => handleResume("takeover_only")}
                        className="px-4 py-2 rounded-md border border-[#D1D5DB] bg-white hover:bg-[#F3F4F6] text-[#374151] text-[13px] font-medium transition-colors disabled:opacity-50"
                      >
                        仅接管对话（不建单）
                      </button>
                    </div>
                  </div>
                )}

                {selectedConv.status === "human_handling" && (
                  <div className="border-t border-[#E5E7EB] p-4 bg-[#FBFBFC] shrink-0 space-y-2.5">
                    <textarea
                      rows={2}
                      value={replyText}
                      onChange={(e) => setReplyText(e.target.value)}
                      placeholder="输入人工坐席回复内容..."
                      className="w-full rounded-md border border-[#D1D5DB] bg-white px-3 py-2 text-[13px] focus:outline-none focus:border-[#1664FF]"
                    />
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        disabled={busy || !replyText.trim()}
                        onClick={() => handleReply(false)}
                        className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[12.5px] font-medium disabled:opacity-40"
                      >
                        <Send className="w-3.5 h-3.5" />
                        发送回复
                      </button>
                      <button
                        type="button"
                        disabled={busy || !replyText.trim()}
                        onClick={() => handleReply(true)}
                        className="px-3.5 py-1.5 rounded-md border border-[#D1D5DB] bg-white hover:bg-[#F3F4F6] text-[#374151] text-[12.5px] font-medium disabled:opacity-40"
                      >
                        回复并结单
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </main>

          {/* 右栏：AI 交接摘要与诊断详情 */}
          {selectedConv && (
            <aside className="w-[390px] shrink-0 border-l border-[#E5E7EB] bg-[#FBFBFC] overflow-y-auto p-4 space-y-4">
              {(selectedQueueItem ||
                selectedConv.handoff_summary ||
                selectedConv.draft_ticket) && (
                <HandoffCard
                  handoffSummary={
                    selectedQueueItem?.handoff_summary ||
                    selectedConv.handoff_summary
                  }
                  draftTicket={
                    selectedQueueItem?.draft_ticket || selectedConv.draft_ticket
                  }
                />
              )}
            </aside>
          )}
        </div>
      )}
    </div>
  );
};
