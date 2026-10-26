import Link from "next/link";

/** 不是管理员（或者管理员身份已被撤销）时显示。真正的拦截在后端，这里只负责告诉用户。 */
export function AccessDenied() {
  return (
    <section role="alert" className="mx-auto mt-24 max-w-sm rounded-2xl bg-white p-8 text-center shadow">
      <h1 className="text-lg font-medium text-gray-900">没有权限访问管理工作台</h1>
      <p className="mt-2 text-sm text-gray-500">只有管理员可以处理工单和退货申请。</p>
      <Link href="/" className="mt-6 inline-block text-sm text-blue-600 hover:text-blue-800">
        返回聊天页
      </Link>
    </section>
  );
}
