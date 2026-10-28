import Link from "next/link";

/** 不是管理员（或者管理员身份已被撤销）时显示。真正的拦截在后端，这里只负责告诉用户。 */
export function AccessDenied() {
  return (
    <section role="alert" className="mx-auto mt-24 max-w-sm rounded-xl border bg-card p-8 text-center shadow-sm">
      <h1 className="text-lg font-medium">没有权限访问管理工作台</h1>
      <p className="mt-2 text-sm text-muted-foreground">只有管理员可以处理工单和退货申请。</p>
      <Link href="/" className="mt-6 inline-block text-sm text-primary hover:underline">
        返回聊天页
      </Link>
    </section>
  );
}
