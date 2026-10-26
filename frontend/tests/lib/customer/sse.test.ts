import { describe, expect, it } from "vitest";

import { readSseEvents, type SseEvent } from "@/lib/customer/sse";

/** 把若干段字节包装成一个“分块到达”的响应体。 */
function streamOf(...chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

const encode = (text: string) => new TextEncoder().encode(text);

async function collect(body: ReadableStream<Uint8Array>): Promise<SseEvent[]> {
  const events: SseEvent[] = [];
  for await (const event of readSseEvents(body)) events.push(event);
  return events;
}

describe("readSseEvents", () => {
  it("一块里有多个事件时逐个解析出来", async () => {
    const body = streamOf(
      encode('event: message.delta\ndata: {"text": "你"}\n\nevent: message.delta\ndata: {"text": "好"}\n\n'),
    );

    expect(await collect(body)).toEqual([
      { event: "message.delta", data: '{"text": "你"}' },
      { event: "message.delta", data: '{"text": "好"}' },
    ]);
  });

  it("一个事件被拆成多块时拼起来再解析", async () => {
    const body = streamOf(
      encode("event: message.do"),
      encode('ne\ndata: {"message_id"'),
      encode(": 7}\n"),
      encode("\n"),
    );

    expect(await collect(body)).toEqual([{ event: "message.done", data: '{"message_id": 7}' }]);
  });

  it("中文字符的字节被拆开时也能正确解码", async () => {
    const bytes = encode('event: message.delta\ndata: {"text": "退货"}\n\n');
    const splitInsideCharacter = bytes.indexOf(encode("退")[0]) + 1;

    const body = streamOf(bytes.slice(0, splitInsideCharacter), bytes.slice(splitInsideCharacter));

    expect(await collect(body)).toEqual([{ event: "message.delta", data: '{"text": "退货"}' }]);
  });

  it("流在事件中途结束时只返回完整的事件", async () => {
    const body = streamOf(
      encode('event: message.accepted\ndata: {"message_id": 1}\n\n'),
      encode('event: message.delta\ndata: {"text": "半'),
    );

    expect(await collect(body)).toEqual([{ event: "message.accepted", data: '{"message_id": 1}' }]);
  });
});
