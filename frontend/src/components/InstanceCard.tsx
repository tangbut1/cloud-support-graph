import React from "react";
import { AlertTriangle } from "lucide-react";
import { InstanceItem } from "../api/client";
import { StatusBadge } from "./StatusBadge";

interface Props {
  instance: InstanceItem;
  onQuickDiagnose?: (instance: InstanceItem) => void;
  actionSlot?: React.ReactNode;
  compact?: boolean;
}

export const InstanceCard: React.FC<Props> = ({
  instance,
  onQuickDiagnose,
  actionSlot,
  compact = false,
}) => {
  const stoppedServices = Object.entries(instance.service_status).filter(
    ([, st]) => st !== "running"
  );
  const isCritical = instance.status !== "running" || stoppedServices.length > 0;

  return (
    <div
      className={`rounded-md border bg-white transition-colors ${
        compact ? "p-3" : "p-4"
      } ${
        isCritical
          ? "border-[#E5E7EB] border-l-2 border-l-red-500"
          : "border-[#E5E7EB] hover:border-[#D1D5DB]"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-[#111827] text-[13px] font-mono truncate">
            {instance.instance_name}
          </div>
          <div className="mt-0.5 text-[11px] text-[#6B7280] font-mono tabular-nums truncate">
            {instance.public_ip || "-"} · {instance.spec || "-"} · {instance.region || "-"}
          </div>
        </div>
        <StatusBadge value={instance.status} subtle />
      </div>

      {/* 关键配置元数据（紧凑键值对，避免堆叠彩色药丸） */}
      <div className="mt-2.5 pt-2 border-t border-[#F3F4F6] grid grid-cols-2 gap-y-1 gap-x-2 text-[11.5px]">
        <div className="text-[#6B7280] truncate">
          端口{" "}
          <span className="font-mono text-[#111827]">
            {instance.firewall_ports.join(", ") || "无"}
          </span>
        </div>
        <div className="text-right tabular-nums">
          <span className="text-[#6B7280]">Swap </span>
          <span
            className={`font-mono ${
              instance.swap_mb === 0 ? "text-amber-700 font-medium" : "text-[#111827]"
            }`}
          >
            {instance.swap_mb}MB
          </span>
        </div>
        <div className="text-[#6B7280] truncate">
          备案{" "}
          <span className={instance.icp_filed ? "text-[#111827]" : "text-amber-700 font-medium"}>
            {instance.icp_filed ? "已备案" : "未备案"}
          </span>
        </div>
        <div className="text-right text-[#6B7280] truncate">
          {Object.entries(instance.service_status)
            .map(([k, v]) => `${k}:${v === "running" ? "正常" : "停止"}`)
            .join(" / ") || "-"}
        </div>
      </div>

      {stoppedServices.length > 0 && (
        <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-red-700 bg-red-50/70 px-2 py-1 rounded">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="truncate">
            异常停止：{stoppedServices.map(([k]) => k).join(", ")}
          </span>
        </div>
      )}

      {(onQuickDiagnose || actionSlot) && (
        <div className="mt-2.5 pt-2 border-t border-[#F3F4F6] flex items-center justify-between gap-2">
          {onQuickDiagnose && (
            <button
              type="button"
              onClick={() => onQuickDiagnose(instance)}
              className="text-[12px] font-medium text-[#1664FF] hover:text-[#0E4FE0] hover:underline"
            >
              诊断此实例
            </button>
          )}
          {actionSlot}
        </div>
      )}
    </div>
  );
};
