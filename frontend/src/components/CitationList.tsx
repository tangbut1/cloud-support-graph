import React, { useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import { CitationDetail } from "../api/client";

interface Props {
  citations: string[];
  details?: CitationDetail[];
}

export const CitationList: React.FC<Props> = ({ citations, details = [] }) => {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  if (!citations || citations.length === 0) return null;

  const findDetail = (tag: string): CitationDetail | undefined =>
    details.find((d) => d.citation === tag);

  return (
    <div className="mt-3 pt-2.5 border-t border-[#EAECEF]">
      <div className="flex items-center gap-1.5 text-[11.5px] text-[#6B7280] mb-1.5">
        <FileText className="w-3.5 h-3.5 text-[#6B7280]" />
        <span>引用官方文档（点击展开原文）</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {citations.map((tag, idx) => {
          const isOpen = openIndex === idx;
          const detail = findDetail(tag);
          return (
            <button
              key={`${tag}-${idx}`}
              type="button"
              onClick={() => setOpenIndex(isOpen ? null : idx)}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11.5px] border transition-colors ${
                isOpen
                  ? "bg-[#111827] text-white border-[#111827]"
                  : "bg-[#F9FAFB] text-[#374151] border-[#E5E7EB] hover:border-[#9CA3AF] hover:text-[#111827]"
              }`}
            >
              <span>{tag}</span>
              {detail &&
                (isOpen ? (
                  <ChevronDown className="w-3 h-3 opacity-80" />
                ) : (
                  <ChevronRight className="w-3 h-3 opacity-60" />
                ))}
            </button>
          );
        })}
      </div>

      {openIndex !== null && (
        <div className="mt-2.5 pl-3 border-l-2 border-[#1664FF] bg-[#F9FAFB] rounded-r py-2.5 pr-3 text-[12.5px] text-[#374151] leading-relaxed">
          {(() => {
            const detail = findDetail(citations[openIndex]);
            if (!detail) {
              return <div className="text-[#6B7280]">来源标注：{citations[openIndex]}</div>;
            }
            return (
              <>
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="font-semibold text-[#111827]">
                    {detail.doc_title}
                    {detail.section_title ? ` · ${detail.section_title}` : ""}
                  </span>
                  {detail.score != null && (
                    <span className="text-[11.5px] font-mono tabular-nums text-[#6B7280]">
                      相关度 {detail.score.toFixed(3)}
                    </span>
                  )}
                </div>
                <div className="whitespace-pre-wrap text-[#4B5563]">{detail.content}</div>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
};
