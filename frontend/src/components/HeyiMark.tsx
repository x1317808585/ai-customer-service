/** 合意的图标：三道笔画。颜色跟随文字颜色，大小由 className 决定。 */
export function HeyiMark({ className }: { className?: string }) {
  return (
    <svg viewBox="60.5 107 48 48" aria-hidden className={className}>
      <g fill="none" stroke="currentColor" strokeWidth="7.6" strokeLinecap="round">
        <path d="M70.8 126.8l2.1 12" />
        <path d="M81.3 116.3l5.1 29" />
        <path d="M94.1 120l3.7 17.7" />
      </g>
    </svg>
  );
}
