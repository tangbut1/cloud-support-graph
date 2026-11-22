import React from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LogOut } from "lucide-react";
import { useAuth } from "../stores/auth";

const ROLE_TEXT: Record<string, string> = {
  customer: "客户",
  agent: "坐席工程师",
  admin: "系统管理员",
};

export const Navbar: React.FC = () => {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  if (!user) return null;

  const navItems: Array<{ to: string; label: string }> = [];
  if (user.role === "customer") {
    navItems.push({ to: "/portal", label: "客户支持" });
  } else if (user.role === "agent") {
    navItems.push({ to: "/agent", label: "坐席工作台" });
  } else if (user.role === "admin") {
    navItems.push(
      { to: "/admin", label: "观测与评测" },
      { to: "/agent", label: "坐席工作台" },
      { to: "/portal", label: "客户视角" }
    );
  }

  return (
    <header className="sticky top-0 z-30 h-[52px] bg-white border-b border-[#E5E7EB] shrink-0">
      <div className="h-full px-5 flex items-center justify-between gap-4">
        <div className="flex items-center h-full gap-5">
          <Link to="/" className="flex items-center gap-2.5 text-[#111827] shrink-0">
            <span className="w-6 h-6 rounded-[5px] bg-[#111827] text-white flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                <rect x="2" y="2.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
                <rect x="9" y="2.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
                <rect x="5.5" y="9" width="5" height="4.5" rx="1" fill="currentColor" />
                <path d="M4.5 7.5V9.5H5.5M11.5 7.5V9.5H10.5" stroke="currentColor" strokeWidth="1.2" />
              </svg>
            </span>
            <span className="text-[14px] font-semibold tracking-tight">CloudSupport</span>
            <span className="hidden sm:inline-block text-[11px] text-[#9CA3AF] font-normal border-l border-[#E5E7EB] pl-2.5">
              云服务分诊与工单控制台
            </span>
          </Link>

          <nav className="flex items-center h-full gap-1 ml-1">
            {navItems.map((item) => {
              const active = location.pathname.startsWith(item.to);
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={`relative h-full inline-flex items-center px-3 text-[13px] transition-colors ${
                    active
                      ? "text-[#111827] font-semibold"
                      : "text-[#6B7280] hover:text-[#111827] font-medium"
                  }`}
                >
                  {item.label}
                  {active && (
                    <span className="absolute bottom-0 left-2 right-2 h-[2px] bg-[#1664FF]" />
                  )}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-3 text-[13px]">
          <div className="flex items-center gap-2">
            <span className="w-6 h-6 rounded-full bg-[#F3F4F6] border border-[#E5E7EB] text-[11px] font-semibold text-[#374151] flex items-center justify-center">
              {user.display_name.slice(-1)}
            </span>
            <span className="font-medium text-[#111827]">{user.display_name}</span>
            {ROLE_TEXT[user.role] && user.display_name !== ROLE_TEXT[user.role] && (
              <span className="text-[12px] text-[#6B7280]">
                {ROLE_TEXT[user.role]}
              </span>
            )}
          </div>
          <span className="h-3.5 w-px bg-[#E5E7EB]" />
          <button
            type="button"
            onClick={() => {
              logout();
              navigate("/login");
            }}
            className="inline-flex items-center gap-1.5 text-[12px] text-[#6B7280] hover:text-[#111827] px-2 py-1 rounded hover:bg-[#F3F4F6] transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span>切换/退出</span>
          </button>
        </div>
      </div>
    </header>
  );
};
