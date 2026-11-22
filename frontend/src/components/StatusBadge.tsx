import React from "react";

interface BadgeSpec {
  label: string;
  dot: string;
  text: string;
  bg?: string;
}

const SPEC_MAP: Record<string, BadgeSpec> = {
  // 实例状态
  running: { label: "运行中", dot: "bg-emerald-500", text: "text-[#065F46]" },
  stopped: { label: "已停止", dot: "bg-red-500", text: "text-[#991B1B]" },
  error: { label: "异常", dot: "bg-red-600", text: "text-[#991B1B]" },

  // 会话状态
  ai_handling: { label: "AI 处理中", dot: "bg-[#1664FF]", text: "text-[#1E40AF]" },
  pending_human: { label: "待坐席接管", dot: "bg-amber-500", text: "text-[#92400E]" },
  human_handling: { label: "人工处理中", dot: "bg-emerald-500", text: "text-[#065F46]" },
  closed: { label: "已关闭", dot: "bg-gray-400", text: "text-[#6B7280]" },

  // 工单状态
  OPEN: { label: "OPEN · 待分诊", dot: "bg-amber-500", text: "text-[#92400E]" },
  TRIAGED: { label: "TRIAGED · 已分诊", dot: "bg-[#1664FF]", text: "text-[#1E40AF]" },
  IN_PROGRESS: { label: "IN_PROGRESS · 处理中", dot: "bg-blue-600", text: "text-[#1E3A8A]" },
  RESOLVED: { label: "RESOLVED · 已解决", dot: "bg-emerald-500", text: "text-[#065F46]" },
  CLOSED: { label: "CLOSED · 已关闭", dot: "bg-gray-400", text: "text-[#6B7280]" },

  // 决策类型
  kb_answer: { label: "有据回答", dot: "bg-emerald-500", text: "text-[#065F46]" },
  kb_refuse: { label: "低分拒答", dot: "bg-amber-500", text: "text-[#92400E]" },
  reject_unsafe: { label: "安全拦截", dot: "bg-red-500", text: "text-[#991B1B]" },
  tool_answer: { label: "诊断直复", dot: "bg-[#1664FF]", text: "text-[#1E40AF]" },
  hitl_ticket_created: { label: "审批建单", dot: "bg-emerald-600", text: "text-[#065F46]" },
  hitl_takeover: { label: "转人工接管", dot: "bg-gray-500", text: "text-[#374151]" },

  // 角色
  customer: { label: "客户", dot: "bg-[#1664FF]", text: "text-[#374151]" },
  agent: { label: "坐席工程师", dot: "bg-emerald-500", text: "text-[#374151]" },
  admin: { label: "系统管理员", dot: "bg-[#111827]", text: "text-[#111827]" },
};

export const StatusBadge: React.FC<{
  value?: string | null;
  className?: string;
  subtle?: boolean;
}> = ({ value, className = "", subtle = false }) => {
  const key = value || "";
  const spec = SPEC_MAP[key] || {
    label: key || "-",
    dot: "bg-gray-400",
    text: "text-[#4B5563]",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[12px] font-medium whitespace-nowrap ${
        subtle ? "" : "px-2 py-0.5 rounded bg-[#F3F4F6]"
      } ${spec.text} ${className}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${spec.dot}`} />
      <span>{spec.label}</span>
    </span>
  );
};
