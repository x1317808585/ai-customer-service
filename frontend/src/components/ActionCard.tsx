import { useEffect, useState, type ReactNode } from "react";
import {
  CircleAlert,
  CircleCheck,
  CircleHelp,
  CircleSlash,
  Clock,
  Headset,
  LoaderCircle,
  Package,
  RefreshCw,
  ShoppingBag,
  TimerOff,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  actionTexts,
  cardState,
  describeCard,
  formatCents,
  formatValidUntil,
  type ActionView,
  type CardState,
  type OrderPreview,
  type ReturnCancellationPreview,
  type ReturnRequestPreview,
  type TicketPreview,
} from "@/lib/actions";

/**
 * readonly：只显示内容和状态，没有按钮（管理端查看聊天记录时用，管理员不能替顾客确认）。
 * interactive：待确认时显示确认和取消按钮（顾客的聊天页用）。
 */
type Props =
  | { mode: "readonly"; action: ActionView }
  | {
      mode: "interactive";
      action: ActionView;
      /** 确认或取消的请求已发出、还没返回 */
      busy: boolean;
      /** 请求和随后的查询都失败了，不知道结果 */
      unverified: boolean;
      /** 正在重新查询结果 */
      checking: boolean;
      onConfirm: (actionId: number) => void;
      onCancel: (actionId: number) => void;
      onRecheck: (actionId: number) => void;
    };

/** 每种状态的名称、图标和配色：文字、图标加少量颜色，不只靠颜色区分。 */
const STATE_STYLES: Record<CardState, { label: string; icon: LucideIcon; className: string }> = {
  pending: { label: "待确认", icon: Clock, className: "bg-status-pending-bg text-status-pending" },
  submitting: { label: "提交中", icon: LoaderCircle, className: "bg-status-neutral-bg text-status-neutral" },
  unverified: { label: "结果待核实", icon: CircleHelp, className: "bg-status-pending-bg text-status-pending" },
  executed: { label: "已提交", icon: CircleCheck, className: "bg-status-success-bg text-status-success" },
  cancelled: { label: "已取消", icon: CircleSlash, className: "bg-status-neutral-bg text-status-neutral" },
  expired: { label: "已过期", icon: TimerOff, className: "bg-status-neutral-bg text-status-neutral" },
  failed: { label: "失败", icon: CircleAlert, className: "bg-status-danger-bg text-status-danger" },
};

const KIND_ICONS: Record<ActionView["kind"], LucideIcon> = {
  order: ShoppingBag,
  return_request: Package,
  return_cancellation: Package,
  ticket: Headset,
};

/** 待确认操作的卡片。内容全部来自服务器计算的预览，不来自模型说的话。 */
export function ActionCard(props: Props) {
  const { action } = props;
  const texts = actionTexts(action);
  const now = useNowUntil(action.status === "pending" ? action.expires_at : null);
  const busy = props.mode === "interactive" && props.busy;
  const unverified = props.mode === "interactive" && props.unverified;
  const state = cardState(action, { submitting: busy, unverified, now });
  const style = STATE_STYLES[state];
  const KindIcon = KIND_ICONS[action.kind];
  const showButtons = props.mode === "interactive" && (state === "pending" || state === "submitting");

  return (
    <article
      aria-label={texts.title}
      data-action-id={action.id}
      className="w-full max-w-md rounded-xl border bg-card p-4 text-sm text-card-foreground shadow-sm"
    >
      <header className="flex items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
          <KindIcon aria-hidden className="size-5" />
        </span>
        <h3 className="flex-1 text-base font-medium">{texts.title}</h3>
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${style.className}`}>
          <style.icon aria-hidden className={`size-3.5 ${state === "submitting" ? "animate-spin" : ""}`} />
          {style.label}
        </span>
      </header>

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5">
        <ActionDetails action={action} />
      </dl>

      <p className={`mt-3 text-[13px] ${state === "failed" ? "text-status-danger" : "text-muted-foreground"}`}>
        {describeCard(action, state)}
      </p>

      {props.mode === "interactive" && state === "unverified" && (
        <div className="mt-4 flex justify-end border-t pt-3">
          <Button
            variant="outline"
            disabled={props.checking}
            onClick={() => props.onRecheck(action.id)}
            className="h-10 gap-1.5 px-4"
          >
            <RefreshCw aria-hidden className={props.checking ? "animate-spin" : ""} />
            重新查询
          </Button>
        </div>
      )}

      {showButtons && (
        <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3">
          <span className="text-xs text-muted-foreground">{formatValidUntil(action.expires_at)}</span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => props.onCancel(action.id)}
              className="h-10 px-4"
            >
              {texts.dismissLabel}
            </Button>
            <Button
              disabled={busy}
              onClick={() => props.onConfirm(action.id)}
              className="h-10 px-4 hover:bg-primary-hover"
            >
              {texts.confirmLabel}
            </Button>
          </div>
        </div>
      )}
    </article>
  );
}

/**
 * 当前时间。给了截止时刻时，到点自动更新一次，让卡片按时切换成“已过期”。
 * 只在到期那一刻更新，不每秒刷新。
 */
function useNowUntil(deadline: string | null): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (deadline === null) return;
    const delay = new Date(deadline).getTime() - Date.now();
    if (delay <= 0 || delay > MAX_TIMEOUT) return;
    const timer = setTimeout(() => setNow(new Date()), delay);
    return () => clearTimeout(timer);
  }, [deadline]);
  return now;
}

/** setTimeout 能接受的最长等待时间，超过会立即触发。 */
const MAX_TIMEOUT = 2 ** 31 - 1;

/** 按操作类型显示预览内容。 */
function ActionDetails({ action }: { action: ActionView }) {
  switch (action.kind) {
    case "order":
      return <OrderDetails preview={action.preview} />;
    case "return_request":
      return <ReturnRequestDetails preview={action.preview} />;
    case "return_cancellation":
      return <ReturnCancellationDetails preview={action.preview} />;
    case "ticket":
      return <TicketDetails preview={action.preview} />;
  }
}

function OrderDetails({ preview }: { preview: OrderPreview }) {
  return (
    <>
      {preview.lines.map((line) => (
        <Row key={line.product_id} label="商品">
          {line.product_name} × {line.quantity}，单价 ¥{formatCents(line.unit_price_cents)}
        </Row>
      ))}
      <Row label="合计">¥{formatCents(preview.total_cents)}</Row>
    </>
  );
}

function ReturnRequestDetails({ preview }: { preview: ReturnRequestPreview }) {
  return (
    <>
      <Row label="订单">
        {preview.order_id}（订单项 {preview.order_item_id}）
      </Row>
      <Row label="商品">
        {preview.product_name} × {preview.quantity}
      </Row>
      <Row label="退款">¥{formatCents(preview.refund_cents)}</Row>
      <Row label="原因">{preview.reason}</Row>
    </>
  );
}

function ReturnCancellationDetails({ preview }: { preview: ReturnCancellationPreview }) {
  return (
    <>
      <Row label="退货单">{preview.return_id}</Row>
      <Row label="订单">{preview.order_id}</Row>
      <Row label="商品">
        {preview.product_name} × {preview.quantity}
      </Row>
      <Row label="退款">¥{formatCents(preview.refund_cents)}</Row>
    </>
  );
}

function TicketDetails({ preview }: { preview: TicketPreview }) {
  return (
    <>
      {preview.order_id !== null && <Row label="订单">{preview.order_id}</Row>}
      <Row label="摘要">{preview.summary}</Row>
    </>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="whitespace-pre-wrap [overflow-wrap:anywhere]">{children}</dd>
    </>
  );
}
