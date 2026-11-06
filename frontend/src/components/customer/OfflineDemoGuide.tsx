"use client";

import { useEffect, useState } from "react";

import { isOfflineAssistant, OFFLINE_EXAMPLES } from "@/lib/customer/offlineDemo";

/**
 * 离线演示模式的说明：后端用内置离线模型时显示，列出支持的说法，点一下把说法填进输入框。
 * 读不到助手的运行方式时不显示，不影响聊天。
 */
export function OfflineDemoGuide({ onPick }: { onPick: (text: string) => void }) {
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let active = true;
    isOfflineAssistant().then(
      (value) => {
        if (active) setOffline(value);
      },
      () => {}, // 读不到就不显示
    );
    return () => {
      active = false;
    };
  }, []);

  if (!offline) {
    return null;
  }
  return (
    <details className="border-b bg-status-neutral-bg px-6 py-2 text-sm text-status-neutral">
      <summary className="cursor-pointer select-none">
        离线演示模式：回复按内置规则生成，不调用外部大模型，只能理解下面这些说法（点开查看）
      </summary>
      <p className="mt-2">先输入“商品”或“我的订单”查看编号，再用编号下单或退货。点一下说法，会把它填进输入框。</p>
      <ul aria-label="支持的说法" className="mt-2 grid gap-1 pb-1 sm:grid-cols-2">
        {OFFLINE_EXAMPLES.map((example) => (
          <li key={example.say}>
            <button
              type="button"
              onClick={() => onPick(example.fill)}
              className="w-full rounded-md px-2 py-1 text-left hover:bg-black/5 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              <span className="font-medium">{example.purpose}</span>：“{example.say}”
              {example.note && <span className="block text-xs opacity-80">{example.note}</span>}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
