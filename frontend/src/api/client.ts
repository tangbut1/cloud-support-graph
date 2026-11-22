/**
 * 统一 HTTP 客户端：自动携带 JWT、遇到 401 清理登录态并跳转登录页、提取中文错误提示。
 */

export interface UserInfo {
  id: number;
  username: string;
  role: "customer" | "agent" | "admin";
  display_name: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserInfo;
}

export interface InstanceItem {
  id: number;
  owner_id: number;
  owner_name?: string | null;
  instance_name: string;
  public_ip?: string | null;
  region?: string | null;
  spec?: string | null;
  os_image?: string | null;
  status: string;
  firewall_ports: number[];
  service_status: Record<string, string>;
  icp_filed: boolean;
  swap_mb: number;
  updated_at: string;
}

export interface CitationDetail {
  citation: string;
  doc_title: string;
  section_title?: string | null;
  content: string;
  score?: number;
}

export interface ConversationItem {
  id: string;
  customer_id: number;
  customer_name?: string | null;
  assigned_agent_id?: number | null;
  thread_id: string;
  title?: string | null;
  status: "ai_handling" | "pending_human" | "human_handling" | "closed";
  handoff_summary?: Record<string, any> | null;
  draft_ticket?: Record<string, any> | null;
  last_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageItem {
  id: number;
  sender_role: "customer" | "assistant" | "agent" | "system";
  content: string;
  has_image: boolean;
  image_url?: string | null;
  citations: string[];
  citation_details?: CitationDetail[];
  trace_id?: number | null;
  created_at: string;
}

export interface AgentQueueItem {
  conversation_id: string;
  customer_name: string;
  updated_at: string;
  handoff_summary: Record<string, any>;
  draft_ticket?: Record<string, any> | null;
}

export interface TicketEvent {
  id: number;
  operator_id?: number | null;
  from_status?: string | null;
  to_status: string;
  comment?: string | null;
  created_at: string;
}

export interface TicketItem {
  id: number;
  ticket_no: string;
  customer_id: number;
  instance_id?: number | null;
  assignee_id?: number | null;
  category?: string | null;
  priority: string;
  status: "OPEN" | "TRIAGED" | "IN_PROGRESS" | "RESOLVED" | "CLOSED";
  title: string;
  description?: string | null;
  ai_diagnosis?: string | null;
  resolution_note?: string | null;
  created_at: string;
  updated_at: string;
  events?: TicketEvent[];
}

export interface KbDocItem {
  id: number;
  title: string;
  category?: string | null;
  version: number;
  updated_at: string;
  chunk_count: number;
}

export interface KbSearchHit {
  chunk_id: number;
  doc_title: string;
  section_title?: string | null;
  content: string;
  bm25_raw: number;
  coverage: number;
  score: number;
  citation: string;
}

export interface KbSearchResponse {
  query: string;
  tokens: string[];
  threshold: number;
  top_score: number;
  accepted: boolean;
  hits: KbSearchHit[];
}

export interface TraceItem {
  id: number;
  conversation_id?: string | null;
  user_id?: number | null;
  intent?: string | null;
  rag_hit_count?: number | null;
  rag_top_score?: number | null;
  tool_calls: string[];
  decision?: string | null;
  latency_ms?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  error_msg?: string | null;
  created_at: string;
}

export interface TraceListResponse {
  items: TraceItem[];
  total: number;
  aggregate: {
    total_calls: number;
    user_calls?: number;
    eval_calls?: number;
    avg_latency_ms: number;
    total_tokens: number;
    decision_distribution: Record<string, number>;
  };
}

export interface EvalRunSummary {
  id: number;
  run_name: string;
  config_json?: Record<string, any> | null;
  total_cases: number;
  passed_cases?: number | null;
  error_cases?: number;
  intent_acc?: number | null;
  refusal_acc?: number | null;
  safe_routing_rate?: number | null;
  security_block_rate?: number | null;
  tool_acc?: number | null;
  tool_ops_acc?: number | null;
  hitl_escalate_acc?: number | null;
  avg_faithfulness?: number | null;
  citation_hit_rate?: number | null;
  judge_mode?: string;
  avg_latency_ms?: number | null;
  status?: string;
  created_at: string;
}

export interface EvalCaseResult {
  id: number;
  case_id: string;
  category?: string | null;
  passed: boolean;
  expected: Record<string, any>;
  actual: Record<string, any>;
  judge_score?: number | null;
  reason?: string | null;
}

export interface EvalRunDetail extends EvalRunSummary {
  cases: EvalCaseResult[];
}

export const TOKEN_KEY = "csg_access_token";
export const USER_KEY = "csg_user_info";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearAuthStorage(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

/** 把 UTC 时间文本 ("YYYY-MM-DD HH:MM:SS") 转为本地易读时间 */
export function formatLocalTime(utcText?: string | null): string {
  if (!utcText) return "-";
  const iso = utcText.includes("T") ? utcText : utcText.replace(" ", "T") + "Z";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return utcText;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers || {});
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    if (token && getToken() === token) {
      clearAuthStorage();
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    throw new ApiError(401, "登录已过期，请重新登录");
  }

  if (!res.ok) {
    let detail = `请求失败 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body?.detail)) {
        detail = body.detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
      }
    } catch {
      // 忽略非 JSON 错误体
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  login: (username: string, password: string) =>
    apiFetch<TokenResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  demoLogin: (username: string) =>
    apiFetch<TokenResponse>("/api/auth/demo-login", {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  authConfig: () =>
    apiFetch<{ enable_demo_login: boolean }>("/api/auth/config"),
  me: () => apiFetch<UserInfo>("/api/auth/me"),

  listInstances: (customerId?: number) =>
    apiFetch<InstanceItem[]>(
      customerId ? `/api/instances?customer_id=${customerId}` : "/api/instances"
    ),
  applyFaultPreset: (instanceId: number, preset: string) =>
    apiFetch<InstanceItem>(`/api/admin/instances/${instanceId}/fault-preset`, {
      method: "PATCH",
      body: JSON.stringify({ preset }),
    }),

  createConversation: (title?: string) =>
    apiFetch<{ id: string; thread_id: string; status: string; created_at: string }>(
      "/api/conversations",
      { method: "POST", body: JSON.stringify({ title }) }
    ),
  listConversations: (status?: string) =>
    apiFetch<ConversationItem[]>(
      status
        ? `/api/conversations?status=${encodeURIComponent(status)}`
        : "/api/conversations"
    ),
  getConversation: (id: string) =>
    apiFetch<ConversationItem>(`/api/conversations/${id}`),
  listMessages: (id: string) =>
    apiFetch<MessageItem[]>(`/api/conversations/${id}/messages`),

  getAgentQueue: () => apiFetch<AgentQueueItem[]>("/api/agent/queue"),
  resumeConversation: (
    id: string,
    action: "approve_ticket_and_reply" | "takeover_only",
    comment?: string
  ) =>
    apiFetch<{
      conversation_status: string;
      ticket_no: string | null;
      message: string;
      forced_takeover?: boolean;
    }>(
      `/api/agent/conversations/${id}/resume`,
      { method: "POST", body: JSON.stringify({ action, comment }) }
    ),
  replyConversation: (id: string, content: string, close = false) =>
    apiFetch<{ message_id: number; conversation_status: string; created_at: string }>(
      `/api/agent/conversations/${id}/reply`,
      { method: "POST", body: JSON.stringify({ content, close }) }
    ),

  listTickets: (status?: string) =>
    apiFetch<TicketItem[]>(
      status ? `/api/tickets?status=${encodeURIComponent(status)}` : "/api/tickets"
    ),
  getTicket: (id: number) => apiFetch<TicketItem>(`/api/tickets/${id}`),
  createTicket: (payload: {
    title: string;
    description?: string;
    instance_id?: number | null;
    category?: string;
    priority?: string;
  }) =>
    apiFetch<TicketItem>("/api/tickets", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  transitionTicket: (id: number, toStatus: string, comment?: string) =>
    apiFetch<TicketItem>(`/api/tickets/${id}/transition`, {
      method: "PATCH",
      body: JSON.stringify({ to_status: toStatus, comment }),
    }),
  getNextTicketStatuses: (id: number) =>
    apiFetch<string[]>(`/api/tickets/${id}/next-statuses`),

  listKbDocs: () => apiFetch<KbDocItem[]>("/api/kb/docs"),
  reindexKb: () =>
    apiFetch<{ documents: number; chunks: number }>("/api/kb/reindex", {
      method: "POST",
    }),
  searchKbPreview: (query: string, topK = 4, minScore?: number) =>
    apiFetch<KbSearchResponse>("/api/kb/search-preview", {
      method: "POST",
      body: JSON.stringify({ query, top_k: topK, min_score: minScore }),
    }),

  listTraces: (params: {
    limit?: number;
    offset?: number;
    intent?: string;
    decision?: string;
    source?: "all" | "user" | "eval";
  }) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    if (params.intent) qs.set("intent", params.intent);
    if (params.decision) qs.set("decision", params.decision);
    if (params.source && params.source !== "all") qs.set("source", params.source);
    return apiFetch<TraceListResponse>(`/api/admin/traces?${qs.toString()}`);
  },
  listEvalRuns: () => apiFetch<EvalRunSummary[]>("/api/admin/eval/runs"),
  getEvalRun: (id: number) => apiFetch<EvalRunDetail>(`/api/admin/eval/runs/${id}`),
  triggerEvalRun: (payload: { run_name?: string; limit?: number }) =>
    apiFetch<{ run_id: number; status: string }>("/api/admin/eval/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
