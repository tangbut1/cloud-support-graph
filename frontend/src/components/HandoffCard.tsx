import React from "react";
import { AlertCircle, CheckCircle2 } from "lucide-react";

interface Props {
  handoffSummary?: Record<string, any> | null;
  draftTicket?: Record<string, any> | null;
  title?: string;
}

export const HandoffCard: React.FC<Props> = ({
  handoffSummary,
  draftTicket,
  title = "AI 交接摘要与工单草稿",
}) => {
  if (!handoffSummary && !draftTicket) return null;

  const checks: Array<{ level: string; code: string; message: string }> =
    Array.isArray(handoffSummary?.checks) ? handoffSummary!.checks : [];
  const actions: string[] = Array.isArray(handoffSummary?.suggested_actions)
    ? handoffSummary!.suggested_actions
    : [];

  return (
    <div className="rounded-md border border-[#E5E7EB] bg-white overflow-hidden">
      <div className="px-3.5 py-2.5 bg-[#F9FAFB] border-b border-[#E5E7EB] flex items-center justify-between">
        <span className="text-[12.5px] font-semibold text-[#111827]">{title}</span>
        {draftTicket?.priority && (
          <span className="text-[11px] font-mono font-semibold text-red-700 bg-red-50 px-1.5 py-0.5 rounded">
            {draftTicket.priority}
          </span>
        )}
      </div>

      <div className="p-3.5 space-y-3 text-[12.5px]">
        {handoffSummary && (
          <dl className="space-y-2 text-[#374151]">
            {handoffSummary.customer_intent && (
              <div>
                <dt className="text-[11px] text-[#6B7280] mb-0.5">客户诉求</dt>
                <dd className="text-[#111827]">{handoffSummary.customer_intent}</dd>
              </div>
            )}
            {handoffSummary.instance && (
              <div>
                <dt className="text-[11px] text-[#6B7280] mb-0.5">目标实例</dt>
                <dd className="font-mono text-[#111827] font-medium">
                  {handoffSummary.instance}
                </dd>
              </div>
            )}
            {handoffSummary.diagnosis && (
              <div>
                <dt className="text-[11px] text-[#6B7280] mb-0.5">诊断结论</dt>
                <dd className="leading-relaxed text-[#1F2937]">
                  {handoffSummary.diagnosis}
                </dd>
              </div>
            )}
          </dl>
        )}

        {checks.length > 0 && (
          <div className="pt-2.5 border-t border-[#F3F4F6] space-y-1.5">
            <div className="text-[11px] text-[#6B7280]">检查项</div>
            {checks.map((c, i) => (
              <div key={i} className="flex items-start gap-2 text-[12px]">
                {c.level === "ok" ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 mt-0.5 shrink-0" />
                ) : (
                  <AlertCircle
                    className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${
                      c.level === "critical" ? "text-red-600" : "text-amber-600"
                    }`}
                  />
                )}
                <div className="min-w-0 leading-snug">
                  <span className="font-mono text-[11px] text-[#6B7280] mr-1.5">
                    {c.code}
                  </span>
                  <span className="text-[#374151]">{c.message}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {actions.length > 0 && (
          <div className="pt-2.5 border-t border-[#F3F4F6]">
            <div className="text-[11px] text-[#6B7280] mb-1">建议处理步骤</div>
            <ol className="list-decimal ml-4 space-y-0.5 text-[12px] text-[#374151]">
              {actions.map((act, idx) => (
                <li key={idx}>{act}</li>
              ))}
            </ol>
          </div>
        )}

        {draftTicket && (
          <div className="pt-2.5 border-t border-[#E5E7EB] space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-[#6B7280]">
                拟建工单草稿（审批后落库）
              </span>
              {draftTicket.category && (
                <span className="text-[11px] text-[#6B7280]">{draftTicket.category}</span>
              )}
            </div>
            <div className="font-semibold text-[#111827] text-[12.5px]">
              {draftTicket.title}
            </div>
            {draftTicket.summary && (
              <div className="text-[12px] text-[#4B5563] leading-relaxed">
                {draftTicket.summary}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
