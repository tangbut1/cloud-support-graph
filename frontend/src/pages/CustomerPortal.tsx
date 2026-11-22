import React, { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowRight,
  ImagePlus,
  Loader2,
  Plus,
  RefreshCw,
  Send,
  X,
} from "lucide-react";
import {
  api,
  CitationDetail,
  ConversationItem,
  formatLocalTime,
  InstanceItem,
  MessageItem,
  TicketItem,
} from "../api/client";
import { streamChat } from "../api/sse";
import { AuthImage } from "../components/AuthImage";
import { CitationList } from "../components/CitationList";
import { HandoffCard } from "../components/HandoffCard";
import { InstanceCard } from "../components/InstanceCard";
import { Navbar } from "../components/Navbar";
import { StatusBadge } from "../components/StatusBadge";
import { TicketTable } from "../components/TicketTable";

const EXAMPLE_QUERIES = [
  { label: "Nginx 502 怎么排查", tag: "知识库问答" },
  { label: "我的实例 ubuntu-web-prod 诊断一下", tag: "MCP 诊断" },
  { label: "帮我申请一张阿里云代金券", tag: "低置信拒答" },
  { label: "帮我查一下别人的实例", tag: "越权拦截" },
];

export const CustomerPortal: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"chat" | "tickets">("chat");
  const [instances, setInstances] = useState<InstanceItem[]>([]);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [activeConv, setActiveConv] = useState<ConversationItem | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [tickets, setTickets] = useState<TicketItem[]>([]);

  const [input, setInput] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [statusTrace, setStatusTrace] = useState<string[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [streamingCitations, setStreamingCitations] = useState<string[]>([]);
  const [streamingHits, setStreamingHits] = useState<CitationDetail[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loadingInit, setLoadingInit] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);

  // 手工提单表单
  const [showNewTicket, setShowNewTicket] = useState(false);
  const [tkTitle, setTkTitle] = useState("");
  const [tkDesc, setTkDesc] = useState("");
  const [tkInstanceId, setTkInstanceId] = useState<string>("");

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const quickDiagnoseLockRef = useRef(false);

  const loadInstances = useCallback(async () => {
    try {
      setInstances(await api.listInstances());
    } catch (e: any) {
      setErrorMsg(e?.message || "加载云实例列表失败");
    }
  }, []);

  const loadTickets = useCallback(async () => {
    try {
      setTickets(await api.listTickets());
    } catch (e: any) {
      setErrorMsg(e?.message || "加载工单列表失败");
    }
  }, []);

  const loadConversations = useCallback(async () => {
    try {
      const list = await api.listConversations();
      setConversations(list);
      return list;
    } catch (e: any) {
      setErrorMsg(e?.message || "加载会话列表失败");
      return [];
    }
  }, []);

  const loadConversationState = useCallback(
    async (convId: string, showSpinner = false) => {
      if (showSpinner) setLoadingMessages(true);
      try {
        const [conv, msgs] = await Promise.all([
          api.getConversation(convId),
          api.listMessages(convId),
        ]);
        setActiveConv(conv);
        setMessages(msgs);
      } catch (e: any) {
        setErrorMsg(e?.message || "同步会话消息失败");
      } finally {
        if (showSpinner) setLoadingMessages(false);
      }
    },
    []
  );

  useEffect(() => {
    let active = true;
    setLoadingInit(true);
    Promise.all([loadInstances(), loadTickets(), loadConversations()])
      .then(([, , list]) => {
        if (active && list.length > 0) {
          setActiveConvId(list[0].id);
        }
      })
      .finally(() => {
        if (active) setLoadingInit(false);
      });
    return () => {
      active = false;
    };
  }, [loadInstances, loadTickets, loadConversations]);

  useEffect(() => {
    if (activeConvId) {
      loadConversationState(activeConvId, true);
    } else {
      setActiveConv(null);
      setMessages([]);
    }
  }, [activeConvId, loadConversationState]);

  useEffect(() => {
    if (
      !activeConvId ||
      !activeConv ||
      (activeConv.status !== "pending_human" &&
        activeConv.status !== "human_handling")
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      loadConversationState(activeConvId);
      loadTickets();
      loadConversations();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [
    activeConvId,
    activeConv?.status,
    loadConversationState,
    loadTickets,
    loadConversations,
  ]);

  useEffect(() => {
    const el = chatScrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, streamingText, statusTrace.length]);

  const handleNewConversation = async (): Promise<string | null> => {
    setErrorMsg(null);
    try {
      const created = await api.createConversation("新咨询会话");
      await loadConversations();
      setActiveConvId(created.id);
      setActiveTab("chat");
      return created.id;
    } catch (e: any) {
      setErrorMsg(e?.message || "创建会话失败");
      return null;
    }
  };

  const handleSend = async (overrideText?: string, overrideConvId?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || streaming) return;
    setErrorMsg(null);

    let convId = overrideConvId ?? activeConvId;
    if (!convId || (!overrideConvId && activeConv?.status === "closed")) {
      convId = await handleNewConversation();
      if (!convId) return;
    }

    const fileToSend = imageFile;
    setInput("");
    setImageFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";

    const tempCustomerMsg: MessageItem = {
      id: Date.now(),
      sender_role: "customer",
      content: text,
      has_image: !!fileToSend,
      citations: [],
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempCustomerMsg]);
    setStreaming(true);
    setStatusTrace([]);
    setStreamingText("");
    setStreamingCitations([]);
    setStreamingHits([]);

    try {
      await streamChat(convId, text, fileToSend, (event, data) => {
        if (event === "status" && data?.message) {
          setStatusTrace((prev) => [...prev, data.message]);
        } else if (event === "citations") {
          setStreamingCitations(
            Array.isArray(data?.citations) ? data.citations : []
          );
          setStreamingHits(Array.isArray(data?.hits) ? data.hits : []);
        } else if (event === "token" && typeof data?.text === "string") {
          setStreamingText((prev) => prev + data.text);
        } else if (event === "interrupt") {
          setActiveConv((prev) =>
            prev
              ? {
                  ...prev,
                  status: "pending_human",
                  handoff_summary: data.handoff_summary,
                  draft_ticket: data.draft_ticket,
                }
              : prev
          );
        } else if (event === "error") {
          setErrorMsg(data?.message || "流式响应出现错误");
        }
      });
    } catch (e: any) {
      setErrorMsg(e?.message || "发送消息失败");
    } finally {
      setStreaming(false);
      setStreamingText("");
      setStreamingCitations([]);
      setStreamingHits([]);
      setStatusTrace([]);
      await Promise.all([
        loadConversationState(convId),
        loadConversations(),
        loadTickets(),
      ]);
    }
  };

  const handleQuickDiagnose = async (inst: InstanceItem) => {
    if (streaming || quickDiagnoseLockRef.current) return;
    quickDiagnoseLockRef.current = true;
    try {
      setActiveTab("chat");
      const created = await api.createConversation(`诊断：${inst.instance_name}`);
      await loadConversations();
      setActiveConvId(created.id);
      setMessages([]);
      await handleSend(`我的实例 ${inst.instance_name} 诊断一下`, created.id);
    } catch (e: any) {
      setErrorMsg(e?.message || "发起实例诊断失败");
    } finally {
      quickDiagnoseLockRef.current = false;
    }
  };

  const handleCreateManualTicket = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tkTitle.trim()) return;
    try {
      await api.createTicket({
        title: tkTitle.trim(),
        description: tkDesc.trim() || undefined,
        instance_id: tkInstanceId ? Number(tkInstanceId) : undefined,
        priority: "P3",
      });
      setTkTitle("");
      setTkDesc("");
      setTkInstanceId("");
      setShowNewTicket(false);
      await loadTickets();
    } catch (err: any) {
      setErrorMsg(err?.message || "手工提单失败");
    }
  };

  return (
    <div className="h-screen flex flex-col bg-white overflow-hidden">
      <Navbar />

      <div className="flex-1 flex min-h-0">
        {/* 左侧资源与会话侧栏（全高细线分割） */}
        <aside className="w-[316px] shrink-0 bg-[#FBFBFC] border-r border-[#E5E7EB] flex flex-col min-h-0">
          {/* 云实例清单 */}
          <div className="p-3.5 border-b border-[#E5E7EB] overflow-y-auto max-h-[56%]">
            <div className="flex items-center justify-between mb-2.5">
              <span className="text-[12px] font-semibold text-[#111827]">
                名下云实例（{instances.length}）
              </span>
              <button
                type="button"
                onClick={loadInstances}
                className="text-[#9CA3AF] hover:text-[#111827] p-0.5"
                title="刷新实例状态"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="space-y-2">
              {loadingInit && instances.length === 0 ? (
                <div className="text-[12px] text-[#9CA3AF] py-4 text-center">
                  正在加载云实例...
                </div>
              ) : instances.length === 0 ? (
                <div className="text-[12px] text-[#9CA3AF] py-4 text-center">
                  暂无云实例
                </div>
              ) : (
                instances.map((inst) => (
                  <InstanceCard
                    key={inst.id}
                    instance={inst}
                    compact
                    onQuickDiagnose={handleQuickDiagnose}
                  />
                ))
              )}
            </div>
          </div>

          {/* 历史会话列表 */}
          <div className="flex-1 flex flex-col min-h-0">
            <div className="px-3.5 py-2.5 border-b border-[#EAECEF] flex items-center justify-between">
              <span className="text-[12px] font-semibold text-[#111827]">
                会话记录
              </span>
              <button
                type="button"
                data-testid="new-conversation-btn"
                onClick={handleNewConversation}
                className="inline-flex items-center gap-1 px-2 py-1 rounded text-[12px] font-medium text-[#1664FF] hover:bg-[#EFF4FF] transition-colors"
              >
                <Plus className="w-3.5 h-3.5" />
                新建会话
              </button>
            </div>
            <div className="flex-1 overflow-y-auto divide-y divide-[#F3F4F6]">
              {loadingInit && conversations.length === 0 ? (
                <div className="text-[12px] text-[#9CA3AF] py-8 text-center">
                  正在加载会话记录...
                </div>
              ) : conversations.length === 0 ? (
                <div className="text-[12px] text-[#9CA3AF] py-8 text-center">
                  暂无历史会话
                </div>
              ) : (
                conversations.map((c) => {
                  const active = activeConvId === c.id;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => {
                        setActiveConvId(c.id);
                        setActiveTab("chat");
                      }}
                      className={`w-full text-left px-3.5 py-2.5 transition-colors relative ${
                        active ? "bg-[#EFF4FF]/70" : "hover:bg-[#F3F4F6]/60"
                      }`}
                    >
                      {active && (
                        <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-[#1664FF]" />
                      )}
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-[11.5px] text-[#6B7280] truncate">
                          {c.title ? c.title : `#${c.id.slice(0, 8)}`}
                        </span>
                        <StatusBadge value={c.status} subtle />
                      </div>
                      <div className="mt-1 text-[12.5px] text-[#1F2937] truncate">
                        {c.last_message || "空白会话"}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </aside>

        {/* 右侧主区 */}
        <main className="flex-1 flex flex-col min-w-0 bg-white">
          {/* 二级导航条 */}
          <div className="h-11 px-5 border-b border-[#E5E7EB] flex items-center justify-between shrink-0">
            <div className="flex items-center h-full gap-5">
              <button
                type="button"
                onClick={() => setActiveTab("chat")}
                className={`relative h-full text-[13px] transition-colors ${
                  activeTab === "chat"
                    ? "text-[#111827] font-semibold"
                    : "text-[#6B7280] hover:text-[#111827]"
                }`}
              >
                智能支持对话
                {activeTab === "chat" && (
                  <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#1664FF]" />
                )}
              </button>
              <button
                type="button"
                data-testid="tab-tickets"
                onClick={() => {
                  setActiveTab("tickets");
                  loadTickets();
                }}
                className={`relative h-full text-[13px] transition-colors tabular-nums ${
                  activeTab === "tickets"
                    ? "text-[#111827] font-semibold"
                    : "text-[#6B7280] hover:text-[#111827]"
                }`}
              >
                我的工单（{tickets.length}）
                {activeTab === "tickets" && (
                  <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#1664FF]" />
                )}
              </button>
            </div>

            {activeTab === "chat" && activeConv && (
              <div className="flex items-center gap-3 text-[12px]">
                <span className="font-mono text-[#9CA3AF]">
                  会话 {activeConv.id.slice(0, 8)}
                </span>
                <StatusBadge value={activeConv.status} />
                <button
                  type="button"
                  onClick={() =>
                    activeConvId && loadConversationState(activeConvId)
                  }
                  className="text-[#6B7280] hover:text-[#111827] inline-flex items-center gap-1"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  同步
                </button>
              </div>
            )}

            {activeTab === "tickets" && (
              <button
                type="button"
                onClick={() => setShowNewTicket((v) => !v)}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[12px] font-medium"
              >
                <Plus className="w-3.5 h-3.5" />
                手工提单
              </button>
            )}
          </div>

          {errorMsg && (
            <div className="mx-5 mt-3 rounded-md bg-red-50 border border-red-200 px-3.5 py-2 text-[12.5px] text-red-700 flex items-center justify-between shrink-0">
              <span>{errorMsg}</span>
              <button type="button" onClick={() => setErrorMsg(null)}>
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {activeTab === "tickets" ? (
            <div className="flex-1 overflow-y-auto p-5 bg-[#F7F8FA] space-y-4">
              {showNewTicket && (
                <form
                  onSubmit={handleCreateManualTicket}
                  className="bg-white rounded-md border border-[#E5E7EB] p-4 space-y-3"
                >
                  <div className="text-[13px] font-semibold text-[#111827]">
                    手工提交技术支持工单
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <input
                      type="text"
                      required
                      value={tkTitle}
                      onChange={(e) => setTkTitle(e.target.value)}
                      placeholder="工单标题（必填）"
                      className="h-9 rounded border border-[#D1D5DB] px-3 text-[13px] focus:outline-none focus:border-[#1664FF]"
                    />
                    <select
                      value={tkInstanceId}
                      onChange={(e) => setTkInstanceId(e.target.value)}
                      className="h-9 rounded border border-[#D1D5DB] px-3 text-[13px] bg-white focus:outline-none focus:border-[#1664FF]"
                    >
                      <option value="">不关联特定实例</option>
                      {instances.map((i) => (
                        <option key={i.id} value={i.id}>
                          {i.instance_name} ({i.public_ip})
                        </option>
                      ))}
                    </select>
                  </div>
                  <textarea
                    rows={2}
                    value={tkDesc}
                    onChange={(e) => setTkDesc(e.target.value)}
                    placeholder="故障现象或排查日志（可选）"
                    className="w-full rounded border border-[#D1D5DB] px-3 py-2 text-[13px] focus:outline-none focus:border-[#1664FF]"
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => setShowNewTicket(false)}
                      className="px-3 py-1.5 rounded border border-[#D1D5DB] text-[12.5px] text-[#4B5563]"
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      className="px-3.5 py-1.5 rounded bg-[#1664FF] text-white text-[12.5px] font-medium"
                    >
                      提交工单
                    </button>
                  </div>
                </form>
              )}
              <TicketTable tickets={tickets} onRefresh={loadTickets} />
            </div>
          ) : (
            <div className="flex-1 flex flex-col min-h-0">
              {/* 消息流：文档级阅读体验，居中限宽 */}
              <div
                ref={chatScrollRef}
                className="flex-1 overflow-y-auto px-6 py-6"
              >
                <div className="max-w-[780px] mx-auto space-y-6">
                  {loadingMessages && messages.length === 0 && !streaming && (
                    <div className="py-12 text-center text-[13px] text-[#9CA3AF]">
                      正在加载会话消息...
                    </div>
                  )}

                  {!loadingMessages && messages.length === 0 && !streaming && (
                    <div className="pt-8 space-y-6">
                      <div>
                        <h2 className="text-[18px] font-semibold text-[#111827] tracking-tight">
                          技术支持与故障分诊
                        </h2>
                        <p className="mt-1.5 text-[13.5px] text-[#6B7280] leading-relaxed">
                          输入故障现象或上传报错截图。系统将优先检索官方运维知识库并附带原文出处；涉及您名下云实例的问题将通过受限诊断工具核查，必要时转接人工工程师。
                        </p>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                        {EXAMPLE_QUERIES.map((item) => (
                          <button
                            key={item.label}
                            type="button"
                            onClick={() => handleSend(item.label)}
                            className="group text-left px-3.5 py-3 rounded-md border border-[#E5E7EB] bg-[#FBFBFC] hover:bg-white hover:border-[#1664FF]/50 transition-colors flex items-center justify-between gap-2"
                          >
                            <div className="min-w-0">
                              <div className="text-[11px] text-[#6B7280] mb-0.5">
                                {item.tag}
                              </div>
                              <div className="text-[13px] text-[#111827] font-medium truncate">
                                {item.label}
                              </div>
                            </div>
                            <ArrowRight className="w-3.5 h-3.5 text-[#9CA3AF] group-hover:text-[#1664FF] shrink-0" />
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {messages.map((m) => {
                    const isCustomer = m.sender_role === "customer";
                    const isAgent = m.sender_role === "agent";

                    if (isCustomer) {
                      return (
                        <div key={m.id} className="flex justify-end">
                          <div className="max-w-[76%] rounded-lg bg-[#F3F4F6] border border-[#E5E7EB] px-4 py-2.5 text-[14px] text-[#111827]">
                            <div className="flex items-center justify-end gap-2 text-[11px] text-[#6B7280] mb-1 tabular-nums">
                              {m.has_image && (
                                <span className="text-[#1664FF] font-medium">
                                  已附报错截图
                                </span>
                              )}
                              <span>{formatLocalTime(m.created_at)}</span>
                            </div>
                            <div className="whitespace-pre-wrap leading-relaxed">
                              {m.content}
                            </div>
                            {m.image_url && <AuthImage src={m.image_url} />}
                          </div>
                        </div>
                      );
                    }

                    return (
                      <div
                        key={m.id}
                        className={`flex items-start gap-3 ${
                          isAgent
                            ? "border-l-2 border-emerald-500 pl-3.5 py-0.5"
                            : ""
                        }`}
                      >
                        <div
                          className={`w-6 h-6 rounded text-[11px] font-semibold flex items-center justify-center shrink-0 mt-0.5 ${
                            isAgent
                              ? "bg-emerald-600 text-white"
                              : "bg-[#111827] text-white"
                          }`}
                        >
                          {isAgent ? "席" : "AI"}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 text-[12px] mb-1.5 tabular-nums">
                            <span className="font-semibold text-[#111827]">
                              {isAgent ? "人工坐席" : "AI 智能分诊助手"}
                            </span>
                            <span className="text-[#9CA3AF]">
                              {formatLocalTime(m.created_at)}
                            </span>
                          </div>
                          <div className="markdown-body">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {m.content}
                            </ReactMarkdown>
                          </div>
                          {m.citations && m.citations.length > 0 && (
                            <CitationList
                              citations={m.citations}
                              details={m.citation_details}
                            />
                          )}
                        </div>
                      </div>
                    );
                  })}

                  {/* 流式进行中 */}
                  {streaming && (
                    <div className="flex items-start gap-3">
                      <div className="w-6 h-6 rounded bg-[#111827] text-white flex items-center justify-center shrink-0 mt-0.5">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      </div>
                      <div className="flex-1 min-w-0 space-y-2.5">
                        {statusTrace.length > 0 && (
                          <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-[#6B7280]">
                            {statusTrace.map((st, idx) => (
                              <span key={idx} className="inline-flex items-center gap-1.5">
                                {idx > 0 && <span className="text-[#D1D5DB]">/</span>}
                                <span
                                  className={
                                    idx === statusTrace.length - 1
                                      ? "text-[#1664FF] font-medium"
                                      : ""
                                  }
                                >
                                  {st}
                                </span>
                              </span>
                            ))}
                          </div>
                        )}
                        {streamingText ? (
                          <div className="markdown-body">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {streamingText}
                            </ReactMarkdown>
                          </div>
                        ) : (
                          <div className="text-[13px] text-[#6B7280]">
                            正在处理请求...
                          </div>
                        )}
                        {streamingCitations.length > 0 && (
                          <CitationList
                            citations={streamingCitations}
                            details={streamingHits}
                          />
                        )}
                      </div>
                    </div>
                  )}

                  {/* 转人工挂起提示 */}
                  {activeConv?.status === "pending_human" && (
                    <div
                      data-testid="pending-human-banner"
                      className="space-y-3 pt-1"
                    >
                      <div className="border-l-2 border-amber-500 bg-[#FFFBEB] border border-amber-200/80 rounded-r-md px-4 py-3 text-[12.5px] text-[#92400E] flex items-center justify-between gap-3">
                        <span>
                          <strong>已转接人工坐席，排队中。</strong>{" "}
                          诊断摘要与拟建工单草稿已同步至技术支持队列，审批通过后将自动展示工单编号。
                        </span>
                        <button
                          type="button"
                          onClick={() =>
                            activeConvId && loadConversationState(activeConvId)
                          }
                          className="px-2.5 py-1 rounded bg-white border border-amber-300 text-[#92400E] text-[12px] font-medium shrink-0 hover:bg-amber-50"
                        >
                          刷新状态
                        </button>
                      </div>
                      <HandoffCard
                        handoffSummary={activeConv.handoff_summary}
                        draftTicket={activeConv.draft_ticket}
                      />
                    </div>
                  )}

                  {activeConv?.status === "human_handling" && (
                    <div className="border-l-2 border-emerald-500 bg-[#ECFDF5]/70 px-3.5 py-2 text-[12.5px] text-[#065F46]">
                      人工坐席已接入本次对话，您发送的消息将直达坐席工程师（AI 已暂停介入）。
                    </div>
                  )}

                  {activeConv?.status === "closed" && (
                    <div className="border border-[#E5E7EB] bg-[#F9FAFB] rounded-md px-4 py-2 text-[12.5px] text-[#6B7280] text-center">
                      本次会话已关闭。如需继续咨询，请点击左侧“新建会话”。
                    </div>
                  )}
                </div>
              </div>

              {/* 底部统一输入框（一体化工具栏） */}
              <div className="border-t border-[#E5E7EB] bg-white px-6 py-3.5 shrink-0">
                <div className="max-w-[780px] mx-auto">
                  {imageFile && (
                    <div className="mb-2 inline-flex items-center gap-2 px-2.5 py-1 rounded bg-[#F3F4F6] border border-[#E5E7EB] text-[12px] text-[#374151]">
                      <ImagePlus className="w-3.5 h-3.5 text-[#1664FF]" />
                      <span className="font-mono">
                        {imageFile.name} ({(imageFile.size / 1024).toFixed(1)} KB)
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          setImageFile(null);
                          if (fileInputRef.current)
                            fileInputRef.current.value = "";
                        }}
                        className="text-[#9CA3AF] hover:text-[#111827]"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}

                  <div className="rounded-lg border border-[#D1D5DB] focus-within:border-[#1664FF] focus-within:ring-2 focus-within:ring-[#1664FF]/15 bg-white transition">
                    <textarea
                      rows={2}
                      data-testid="chat-input"
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          handleSend();
                        }
                      }}
                      disabled={streaming || activeConv?.status === "closed"}
                      placeholder="描述云服务报错、实例故障或咨询运维文档..."
                      className="w-full resize-none bg-transparent px-3.5 pt-2.5 pb-1 text-[13.5px] text-[#111827] placeholder:text-[#9CA3AF] focus:outline-none disabled:text-[#9CA3AF]"
                    />
                    <div className="px-2.5 pb-2 pt-1 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          className="hidden"
                          onChange={(e) =>
                            setImageFile(e.target.files?.[0] || null)
                          }
                        />
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          disabled={streaming || activeConv?.status === "closed"}
                          className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[12px] text-[#6B7280] hover:text-[#111827] hover:bg-[#F3F4F6] disabled:opacity-40"
                        >
                          <ImagePlus className="w-3.5 h-3.5" />
                          <span>附报错截图</span>
                        </button>
                      </div>

                      <div className="flex items-center gap-3">
                        <span className="hidden sm:inline text-[11px] text-[#9CA3AF]">
                          Enter 发送 · Shift+Enter 换行
                        </span>
                        <button
                          type="button"
                          data-testid="chat-send-btn"
                          onClick={() => handleSend()}
                          disabled={
                            streaming ||
                            !input.trim() ||
                            activeConv?.status === "closed"
                          }
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-[#1664FF] hover:bg-[#0E4FE0] text-white text-[12.5px] font-medium transition-colors disabled:opacity-40"
                        >
                          <Send className="w-3.5 h-3.5" />
                          发送
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
};
