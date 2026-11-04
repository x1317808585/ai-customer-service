from app.citations import Citation, cited, quote

RETURN_WINDOW = Citation(
    source_id="return-policy#退货期限",
    document_title="退货政策",
    section="退货期限",
    content="签收后 7 天内可以申请退货。",
)
FAQ_COPY = Citation(
    source_id="faq#能退货吗",
    document_title="常见问题",
    section="能退货吗",
    content="签收后 7 天内可以申请退货。",
)
SHIPPING = Citation(source_id="shipping#运费", document_title="配送说明", section="运费", content="所有订单包邮。")


def test_only_quoted_candidates_are_cited_in_order_once_each() -> None:
    reply = f"{quote(SHIPPING.content, SHIPPING.source_id)}；另外{quote(RETURN_WINDOW.content, RETURN_WINDOW.source_id)}"
    reply += f"，再说一遍：{quote(SHIPPING.content, SHIPPING.source_id)}"

    assert cited(reply, [RETURN_WINDOW, SHIPPING, FAQ_COPY]) == [SHIPPING, RETURN_WINDOW]


def test_same_text_in_two_sections_cites_only_the_one_marked() -> None:
    """两个小节原文相同，回复只标了“退货政策”那一节，就只保存那一节。"""
    reply = f"根据退货政策：{quote(RETURN_WINDOW.content, RETURN_WINDOW.source_id)}"

    assert cited(reply, [FAQ_COPY, RETURN_WINDOW]) == [RETURN_WINDOW]


def test_mark_outside_the_candidates_is_ignored() -> None:
    reply = quote("所有订单包邮。", "shipping#运费")

    assert cited(reply, [RETURN_WINDOW]) == []


def test_mark_without_the_exact_text_in_front_is_ignored() -> None:
    paraphrased = "「签收后一周内可以退货」[来源：return-policy#退货期限]"
    unquoted = f"{RETURN_WINDOW.content}[来源：{RETURN_WINDOW.source_id}]"

    assert cited(paraphrased, [RETURN_WINDOW]) == []
    assert cited(unquoted, [RETURN_WINDOW]) == []
