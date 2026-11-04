import { describe, expect, it } from "vitest";

import { splitCitations, type Citation } from "@/lib/citations";

const RETURN_WINDOW: Citation = {
  source_id: "return-policy#退货期限",
  document_title: "退货政策",
  section: "退货期限",
  content: "签收后 7 天内可以申请退货。",
};
const SHIPPING: Citation = {
  source_id: "shipping#运费",
  document_title: "配送说明",
  section: "运费",
  content: "所有订单包邮。",
};

describe("splitCitations", () => {
  it("已核对的标记按引用顺序换成编号，其余文字原样保留", () => {
    const content = "包邮：「所有订单包邮。」[来源：shipping#运费]；退货：「签收后 7 天内可以申请退货。」[来源：return-policy#退货期限]。";

    expect(splitCitations(content, [RETURN_WINDOW, SHIPPING])).toEqual([
      { kind: "text", text: "包邮：「所有订单包邮。」" },
      { kind: "citation", number: 2 },
      { kind: "text", text: "；退货：「签收后 7 天内可以申请退货。」" },
      { kind: "citation", number: 1 },
      { kind: "text", text: "。" },
    ]);
  });

  it("同一个来源标识出现两次，只有前面是逐字原文的那一处编号", () => {
    const content = "「签收后一周内能退」[来源：return-policy#退货期限]，原文：「签收后 7 天内可以申请退货。」[来源：return-policy#退货期限]";

    expect(splitCitations(content, [RETURN_WINDOW])).toEqual([
      { kind: "text", text: "「签收后一周内能退」[来源：return-policy#退货期限]，原文：「签收后 7 天内可以申请退货。」" },
      { kind: "citation", number: 1 },
    ]);
  });

  it("没有引用时整段都是文字", () => {
    expect(splitCitations("（离线模式）查到 0 条订单。", [])).toEqual([
      { kind: "text", text: "（离线模式）查到 0 条订单。" },
    ]);
  });
});
