"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { AccessDenied } from "@/components/admin/AccessDenied";
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
    return <p role="alert" className="p-6 text-sm text-red-700">{error}</p>;
  }
  if (!user) {
    return null;
  }
  if (user.role !== "admin") {
    return (
      <main className="min-h-screen bg-gray-50 px-4">
        <AccessDenied />
      </main>
    );
  }
  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <header className="flex items-center gap-6 border-b border-gray-200 bg-white px-6 py-3 text-sm">
        <span className="font-medium text-gray-900">管理工作台</span>
        <nav aria-label="管理工作台" className="flex gap-1">
          {SECTIONS.map((section) => {
            const current = pathname.startsWith(section.href);
            return (
              <Link
                key={section.href}
                href={section.href}
                aria-current={current ? "page" : undefined}
                className={`rounded-lg px-3 py-1.5 ${current ? "bg-blue-100 text-blue-900" : "text-gray-600 hover:bg-gray-100"}`}
              >
                {section.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-4">
          <Link href="/" className="text-gray-600 hover:text-gray-900">
            聊天页
          </Link>
          <span className="text-gray-500">{user.username}</span>
          <button type="button" onClick={logOut} className="text-gray-500 hover:text-gray-900">
            退出
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
