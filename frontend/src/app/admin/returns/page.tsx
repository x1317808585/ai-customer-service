import type { Metadata } from "next";
import { Suspense } from "react";

import { ReturnDesk } from "@/components/admin/ReturnDesk";

/**
 * 不参与即时导航校验：管理工作台要先确认管理员身份才渲染页面（见 AdminShell），
 * 预渲染校验时看不到页面内容。这不会缩短等待身份确认的时间。
 */
export const instant = false;

export const metadata: Metadata = { title: "退货申请 · 管理工作台" };

/** ReturnDesk 读取地址栏参数，按 Next.js 的要求包在 Suspense 里，否则生产构建会失败。 */
export default function ReturnsPage() {
  return (
    <Suspense fallback={<p role="status" className="p-6 text-sm text-muted-foreground">加载中…</p>}>
      <ReturnDesk />
    </Suspense>
  );
}
