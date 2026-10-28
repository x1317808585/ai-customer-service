"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { LogOut } from "lucide-react";

import { AccessDenied } from "@/components/admin/AccessDenied";
import { Button } from "@/components/ui/button";
import { useCurrentUser } from "@/hooks/useCurrentUser";

const SECTIONS = [
  { href: "/admin/tickets", label: "工单" },
  { href: "/admin/returns", label: "退货申请" },
];

/**
 * 管理工作台的外框：确认是管理员之后才显示页面内容，页面里的管理接口请求也是在这之后才发出。
 * 没登录跳到登录页；不是管理员显示没有权限。后端每次请求都会再检查一次角色。
 */
export function AdminShell({ children }: { children: ReactNode }) {
  const { user, error, logOut } = useCurrentUser();
  const pathname = usePathname();

  if (error) {
    return <p role="alert" className="p-6 text-sm text-status-danger">{error}</p>;
  }
  if (!user) {
    return null;
  }
  if (user.role !== "admin") {
    return (
      <main className="min-h-screen px-4">
        <AccessDenied />
      </main>
    );
  }
  return (
    <div className="flex h-screen flex-col">
      <header className="flex h-16 shrink-0 items-center gap-6 border-b bg-card px-6 text-sm">
        <span className="flex items-baseline gap-2">
          <span className="text-lg font-semibold tracking-tight">合意</span>
          <span className="text-muted-foreground">管理工作台</span>
        </span>
        <nav aria-label="管理工作台" className="flex gap-1">
          {SECTIONS.map((section) => {
            const current = pathname.startsWith(section.href);
            return (
              <Link
                key={section.href}
                href={section.href}
                aria-current={current ? "page" : undefined}
                className={`rounded-lg px-3 py-2 transition-colors duration-150 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none ${current ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground"}`}
              >
                {section.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-4">
          <Link href="/" className="text-primary hover:underline">
            聊天页
          </Link>
          <span className="text-muted-foreground">{user.username}</span>
          <Button variant="ghost" onClick={logOut} className="h-8 gap-1 text-muted-foreground">
            <LogOut aria-hidden />
            退出
          </Button>
        </div>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
