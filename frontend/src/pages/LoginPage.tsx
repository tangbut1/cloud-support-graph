import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { api } from "../api/client";
import { LoginVisualPanel } from "../components/LoginVisualPanel";
import { roleHomePath, useAuth } from "../stores/auth";

const DEMO_ACCOUNTS = [
  {
    username: "zhangsan",
    name: "客户张三",
    role: "客户",
    note: "3 台云实例 · 含 502 叠加故障",
  },
  {
    username: "lisi",
    name: "客户李四",
    role: "客户",
    note: "2 台云实例 · 跨租户隔离对照",
  },
  {
    username: "wanggong",
    name: "坐席王工",
    role: "坐席",
    note: "待接管审批 · 断点建单 · 工单流转",
  },
  {
    username: "admin",
    name: "系统管理员",
    role: "管理员",
    note: "Trace 看板 · 32 题评测 · 故障注入",
  },
];

export const LoginPage: React.FC = () => {
  const { login, demoLogin } = useAuth();
  const navigate = useNavigate();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [demoEnabled, setDemoEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .authConfig()
      .then((cfg) => {
        if (active) setDemoEnabled(Boolean(cfg.enable_demo_login));
      })
      .catch(() => {
        if (active) setDemoEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = await login(username.trim(), password);
      navigate(roleHomePath(user.role), { replace: true });
    } catch (err: any) {
      setError(err?.message || "登录失败");
    } finally {
      setBusy(false);
    }
  };

  const handleDemo = async (demoUser: string) => {
    setError(null);
    setBusy(true);
    try {
      const user = await demoLogin(demoUser);
      navigate(roleHomePath(user.role), { replace: true });
    } catch (err: any) {
      setError(err?.message || "演示登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen grid grid-cols-1 lg:grid-cols-12 bg-white">
      {/* 左侧：克制极简的黑白灰控制台登录区 */}
      <div className="lg:col-span-5 flex flex-col justify-between px-8 sm:px-12 py-8 border-b lg:border-b-0 lg:border-r border-[#E4E4E7] bg-white">
        <div className="flex items-center gap-2.5">
          <span className="w-6 h-6 rounded-[5px] bg-[#18181B] text-white flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="2.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
              <rect x="9" y="2.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
              <rect x="5.5" y="9" width="5" height="4.5" rx="1" fill="currentColor" />
              <path d="M4.5 7.5V9.5H5.5M11.5 7.5V9.5H10.5" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </span>
          <span className="text-[14px] font-semibold text-[#18181B] tracking-tight">
            CloudSupport
          </span>
        </div>

        <div className="w-full max-w-[380px] mx-auto my-10 space-y-6">
          <div>
            <h1 className="text-[22px] font-semibold text-[#18181B] tracking-tight">
              系统登录
            </h1>
            <p className="mt-1 text-[13px] text-[#71717A]">
              云服务智能分诊与工单协作控制台
            </p>
          </div>

          {error && (
            <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-[12.5px] text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-[12.5px] font-medium text-[#3F3F46] mb-1.5">
                账号
              </label>
              <input
                type="text"
                data-testid="login-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                className="w-full h-9 rounded-md border border-[#D4D4D8] bg-white px-3 text-[13.5px] text-[#18181B] placeholder:text-[#A1A1AA] focus:outline-none focus:border-[#18181B] focus:ring-2 focus:ring-[#18181B]/10 transition-colors"
                placeholder="请输入用户名"
              />
            </div>

            <div>
              <label className="block text-[12.5px] font-medium text-[#3F3F46] mb-1.5">
                密码
              </label>
              <input
                type="password"
                data-testid="login-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full h-9 rounded-md border border-[#D4D4D8] bg-white px-3 text-[13.5px] text-[#18181B] placeholder:text-[#A1A1AA] focus:outline-none focus:border-[#18181B] focus:ring-2 focus:ring-[#18181B]/10 transition-colors"
                placeholder="请输入密码"
              />
            </div>

            <button
              type="submit"
              data-testid="login-submit"
              disabled={busy}
              className="w-full h-9 rounded-md bg-[#18181B] hover:bg-[#27272A] active:bg-[#09090B] text-white text-[13.5px] font-medium transition-colors shadow-[0_1px_2px_rgba(0,0,0,0.08)] disabled:opacity-50"
            >
              {busy ? "正在验证..." : "登录控制台"}
            </button>
          </form>

          {/* 仅在服务端开启 ENABLE_DEMO_LOGIN=true 时展示演示快捷入口 */}
          {demoEnabled && (
            <div className="pt-4 border-t border-[#E4E4E7]">
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-[12px] font-medium text-[#71717A]">
                  演示环境快捷进入
                </span>
                <span className="text-[11px] font-mono text-[#A1A1AA]">
                  DEMO_LOGIN
                </span>
              </div>
              <div className="border border-[#E4E4E7] rounded-md divide-y divide-[#F4F4F5] overflow-hidden">
                {DEMO_ACCOUNTS.map((acc) => (
                  <button
                    key={acc.username}
                    type="button"
                    disabled={busy}
                    data-testid={`demo-login-${acc.username}`}
                    onClick={() => handleDemo(acc.username)}
                    className="w-full px-3 py-2.5 bg-white hover:bg-[#FAFAFA] flex items-center justify-between gap-3 text-left transition-colors group disabled:opacity-50"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-medium text-[#18181B]">
                          {acc.name}
                        </span>
                        <span className="text-[11px] text-[#52525B] bg-[#F4F4F5] px-1.5 py-0.5 rounded">
                          {acc.role}
                        </span>
                      </div>
                      <div className="text-[11.5px] text-[#71717A] truncate mt-0.5">
                        {acc.note}
                      </div>
                    </div>
                    <ArrowRight className="w-3.5 h-3.5 text-[#A1A1AA] group-hover:text-[#18181B] group-hover:translate-x-0.5 transition shrink-0" />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="h-6" aria-hidden="true" />
      </div>

      {/* 右侧：亮色极简银灰缓慢滚动波浪粒子网 */}
      <LoginVisualPanel />
    </div>
  );
};
