import React, { createContext, useContext, useEffect, useState } from "react";
import {
  api,
  clearAuthStorage,
  TOKEN_KEY,
  TokenResponse,
  USER_KEY,
  UserInfo,
} from "../api/client";

interface AuthContextValue {
  user: UserInfo | null;
  token: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<UserInfo>;
  demoLogin: (username: string) => Promise<UserInfo>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );
  const [user, setUser] = useState<UserInfo | null>(() => {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as UserInfo;
    } catch {
      return null;
    }
  });
  const [loading, setLoading] = useState<boolean>(!!localStorage.getItem(TOKEN_KEY));

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    let active = true;
    api
      .me()
      .then((me) => {
        if (!active) return;
        setUser(me);
        localStorage.setItem(USER_KEY, JSON.stringify(me));
      })
      .catch(() => {
        if (!active) return;
        clearAuthStorage();
        setToken(null);
        setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [token]);

  const applyToken = (res: TokenResponse): UserInfo => {
    localStorage.setItem(TOKEN_KEY, res.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(res.user));
    setToken(res.access_token);
    setUser(res.user);
    return res.user;
  };

  const login = async (username: string, password: string) => {
    const res = await api.login(username, password);
    return applyToken(res);
  };

  const demoLogin = async (username: string) => {
    const res = await api.demoLogin(username);
    return applyToken(res);
  };

  const logout = () => {
    clearAuthStorage();
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{ user, token, loading, login, demoLogin, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}

export function roleHomePath(role: UserInfo["role"]): string {
  if (role === "agent") return "/agent";
  if (role === "admin") return "/admin";
  return "/portal";
}
