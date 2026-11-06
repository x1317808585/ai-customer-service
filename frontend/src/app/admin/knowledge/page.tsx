import type { Metadata } from "next";

import { KnowledgeDesk } from "@/components/admin/KnowledgeDesk";

/**
 * 不参与即时导航校验：管理工作台要先确认管理员身份才渲染页面（见 AdminShell），
 * 预渲染校验时看不到页面内容。这不会缩短等待身份确认的时间。
 */
export const instant = false;

export const metadata: Metadata = { title: "知识库 · 管理工作台" };

export default function KnowledgePage() {
  return <KnowledgeDesk />;
}
