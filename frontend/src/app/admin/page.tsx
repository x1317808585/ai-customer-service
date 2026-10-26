import { redirect } from "next/navigation";

/**
 * 不参与即时导航校验：管理工作台要先确认管理员身份才渲染页面（见 AdminShell），
 * 预渲染校验时看不到页面内容。这不会缩短等待身份确认的时间。
 */
export const instant = false;

/** /admin 没有单独的内容，直接进入工单页。 */
export default function AdminHomePage() {
  redirect("/admin/tickets");
}
