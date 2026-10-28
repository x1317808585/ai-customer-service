"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { describeError, login } from "@/lib/api";

const FIELD =
  "h-10 w-full rounded-lg border border-input bg-card px-3 text-sm outline-none transition-shadow duration-150 placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/25";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const user = await login(username, password);
      // 按角色进入各自的主页：顾客进聊天页，管理员进管理工作台
      window.location.replace(user.role === "admin" ? "/admin" : "/");
    } catch (reason) {
      setError(describeError(reason));
      setSubmitting(false);
    }
  }

  return (
    <main className="flex h-screen items-center justify-center px-4">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-5 rounded-xl border bg-card p-8 shadow-sm">
        <header className="space-y-1 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">合意</h1>
          <p className="text-sm text-muted-foreground">登录后开始咨询</p>
        </header>
        <div className="space-y-1.5">
          <label htmlFor="login-username" className="block text-sm font-medium">
            用户名
          </label>
          <input
            id="login-username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="用户名"
            autoComplete="username"
            required
            className={FIELD}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="login-password" className="block text-sm font-medium">
            密码
          </label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="密码"
            autoComplete="current-password"
            required
            className={FIELD}
          />
        </div>
        {error && (
          <p role="alert" className="rounded-lg bg-status-danger-bg px-3 py-2 text-sm text-status-danger">
            {error}
          </p>
        )}
        <Button type="submit" disabled={submitting} className="h-10 w-full hover:bg-primary-hover">
          {submitting ? "登录中…" : "登录"}
        </Button>
      </form>
    </main>
  );
}
