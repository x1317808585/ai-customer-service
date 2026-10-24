# AI 客服

面向电商的 AI 客服：顾客用自然语言咨询商品、查询和办理订单售后。

## Language

**User（用户）**:
登录系统的人，分为顾客和管理员两种角色。
_Avoid_: account, member, customer（顾客只是用户的一种角色）

**Conversation（会话）**:
用户与 AI 客服之间的一次连续对话，包含有序的消息。
_Avoid_: chat, session, thread, dialog

**Message（消息）**:
会话中的一条发言，发言方是用户或助手。
_Avoid_: chat record, utterance

**Assistant（助手）**:
系统中代表 AI 客服发言的一方。
_Avoid_: bot, agent（agent 专指后端的编排程序）

**Reply（回复）**:
助手针对一条用户消息生成的消息。生成过程中逐段送达，完整生成后才作为消息保存。
_Avoid_: answer, response

**Product（商品）**:
店里可以买的东西，有价格、库存，并标明是否支持退货。
_Avoid_: item, goods, SKU（暂不区分规格）

**Stock（库存）**:
商品当前还能卖出的件数，永远不小于 0，下单成功时扣减。
_Avoid_: inventory

**Order（订单）**:
用户一次下单的记录，包含一个或多个订单项。状态有：已下单 placed、已发货 shipped、已签收 delivered、已取消 cancelled。只有已签收的订单才有签收时间。
_Avoid_: purchase, deal

**Order Line（下单行）**:
下单请求里的一行：买哪个商品、买几件。不带价格，价格一律以数据库为准。
_Avoid_: cart item

**Order Item（订单项）**:
订单里的一行：哪个商品、买了几件、单价多少。商品名和单价是下单那一刻的快照，之后商品改名或改价都不影响。
_Avoid_: line item

金额一律用"分"为单位的整数保存（59900 表示 599.00 元），避免小数计算误差。
