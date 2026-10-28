import Link from "next/link";
import type { ReactNode } from "react";

import type { ListView } from "@/hooks/admin/useAdminDesk";
import { reachedListLimit, STAFF_LIST_LIMIT } from "@/lib/admin/desk";

type Props<Status extends string, Item extends { id: number }> = {
  /** 列表的名称，比如“工单”，也用作筛选导航的无障碍名称 */
  title: string;
  statuses: readonly Status[];
  statusLabels: Record<Status, string>;
  currentStatus: Status;
  selectedId: number | null;
  list: ListView<Item>;
  hrefFor: (status: Status, id?: number) => string;
  renderItem: (item: Item) => ReactNode;
  onRefresh: () => void;
};

/** 左侧列表：按状态筛选、手动刷新、选中某条记录。筛选和选中都是链接，地址栏随之变化。 */
export function RecordList<Status extends string, Item extends { id: number }>({
  title,
  statuses,
  statusLabels,
  currentStatus,
  selectedId,
  list,
  hrefFor,
  renderItem,
  onRefresh,
}: Props<Status, Item>) {
  return (
    <>
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        <nav aria-label={`${title}筛选`} className="flex flex-wrap gap-1 text-sm">
          {statuses.map((status) => (
            <Link
              key={status}
              href={hrefFor(status)}
              scroll={false}
              aria-current={status === currentStatus ? "page" : undefined}
              className={`rounded-lg px-2.5 py-1 transition-colors duration-150 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none ${
                status === currentStatus
                  ? "bg-accent font-medium text-accent-foreground"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              }`}
            >
              {statusLabels[status]}
            </Link>
          ))}
        </nav>
        <button
          type="button"
          onClick={onRefresh}
          className="shrink-0 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          刷新
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ListBody
          title={title}
          statusLabel={statusLabels[currentStatus]}
          currentStatus={currentStatus}
          selectedId={selectedId}
          list={list}
          hrefFor={hrefFor}
          renderItem={renderItem}
        />
      </div>
    </>
  );
}

function ListBody<Status extends string, Item extends { id: number }>({
  title,
  statusLabel,
  currentStatus,
  selectedId,
  list,
  hrefFor,
  renderItem,
}: {
  title: string;
  statusLabel: string;
  currentStatus: Status;
  selectedId: number | null;
  list: ListView<Item>;
  hrefFor: (status: Status, id?: number) => string;
  renderItem: (item: Item) => ReactNode;
}) {
  if (list.kind === "loading") {
    return <p role="status" className="p-4 text-sm text-muted-foreground">加载中…</p>;
  }
  if (list.kind === "error") {
    return <p role="alert" className="p-4 text-sm text-status-danger">{list.message}</p>;
  }
  return (
    <>
      {list.items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">暂无{statusLabel}的{title}</p>
      ) : (
        <ul aria-label={`${statusLabel}的${title}`}>
          {list.items.map((item) => (
            <li key={item.id}>
              <Link
                href={hrefFor(currentStatus, item.id)}
                scroll={false}
                aria-current={item.id === selectedId ? "true" : undefined}
                className={`block border-b px-4 py-3 text-sm transition-colors duration-150 focus-visible:bg-secondary focus-visible:outline-none ${
                  item.id === selectedId ? "bg-accent" : "hover:bg-secondary/60"
                }`}
              >
                {renderItem(item)}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="px-4 py-3 text-xs text-muted-foreground">按编号从新到旧，最多显示 {STAFF_LIST_LIMIT} 条</p>
      {reachedListLimit(list.items.length) && (
        <p className="px-4 pb-3 text-xs text-status-pending">
          已达到显示上限，可能还有更早的记录，当前没有提供分页。
        </p>
      )}
    </>
  );
}
