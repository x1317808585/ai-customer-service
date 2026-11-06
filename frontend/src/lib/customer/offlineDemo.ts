/**
 * 离线演示模式：内置的离线模型按固定规则回复，只认下面这些说法，和后端 app/agent/llm.py 里的离线规则对应。
 * 编号（商品、订单项、退货申请）每台机器上不一样，不写死，先查出来再用。
 */

import { request } from "@/lib/api";

/** 助手是不是内置的离线模型（不调用外部大模型 API）。 */
export async function isOfflineAssistant(): Promise<boolean> {
  const { offline } = (await (await request("/api/assistant")).json()) as { offline: boolean };
  return offline;
}

export type OfflineExample = {
  /** 用途，比如“下单” */
  purpose: string;
  /** 怎么说，N 表示要换成编号 */
  say: string;
  /** 点击后填进输入框的文字。带编号的只填前半句，编号由用户补上 */
  fill: string;
  note?: string;
};

export const OFFLINE_EXAMPLES: OfflineExample[] = [
  { purpose: "查看商品和商品编号", say: "商品", fill: "商品" },
  { purpose: "查看订单和订单项编号", say: "我的订单", fill: "我的订单" },
  { purpose: "下单", say: "下单 商品 N", fill: "下单 商品 ", note: "N 换成商品编号；买多件时加上“2 件”" },
  { purpose: "申请退货", say: "退货 订单项 N", fill: "退货 订单项 ", note: "N 是订单项编号，不是订单号" },
  { purpose: "查看退货申请", say: "我的退货申请", fill: "我的退货申请" },
  { purpose: "撤回退货申请", say: "撤回退货 N", fill: "撤回退货 ", note: "N 是退货申请编号" },
  { purpose: "转人工", say: "转人工：问题描述", fill: "转人工：", note: "写上“订单 N”会关联这个订单" },
  { purpose: "查看工单进度", say: "我的工单", fill: "我的工单" },
  {
    purpose: "查政策和说明书",
    say: "退货政策是什么",
    fill: "退货政策是什么",
    note: "含“政策、规则、规定、说明、保修、运费”这些词时查知识库",
  },
];
