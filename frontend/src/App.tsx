import React from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, roleHomePath, useAuth } from "./stores/auth";
import { AdminEvalPage } from "./pages/AdminEvalPage";
import { AgentWorkbench } from "./pages/AgentWorkbench";
import { CustomerPortal } from "./pages/CustomerPortal";
import { LoginPage } from "./pages/LoginPage";

const RequireRole: React.FC<{
  allowed: Array<"customer" | "agent" | "admin">;
  children: React.ReactElement;
}> = ({ allowed, children }) => {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm text-slate-500">
        正在校验登录态...
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (!allowed.includes(user.role)) {
    return <Navigate to={roleHomePath(user.role)} replace />;
  }
  return children;
};

const RootRedirect: React.FC = () => {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={roleHomePath(user.role)} replace />;
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/portal"
            element={
              <RequireRole allowed={["customer", "admin"]}>
                <CustomerPortal />
              </RequireRole>
            }
          />
          <Route
            path="/agent"
            element={
              <RequireRole allowed={["agent", "admin"]}>
                <AgentWorkbench />
              </RequireRole>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireRole allowed={["admin"]}>
                <AdminEvalPage />
              </RequireRole>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
};

export default App;
