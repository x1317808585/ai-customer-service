from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

SYSTEM_PROMPT = "你是电商平台的 AI 客服，用简洁、礼貌的中文回答顾客的问题。"


def build_chat_graph(model: BaseChatModel) -> CompiledStateGraph:
    """搭建对话流程：用户消息 → 模型回复。模型由调用方传入，这里不关心它是真是假。"""

    async def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = await model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile()
