export type SseEvent = {
  event: string;
  data: string;
};

/**
 * 把 fetch 响应体（字节流）解析成一个个 SSE 事件。
 *
 * 网络传输会把数据随意切块：一个事件可能分好几块到达，一块里也可能有好几个事件，
 * 一个中文字符的字节甚至会被拆开。所以先把字节解码成文本、攒进缓冲区，
 * 每遇到一个空行（事件之间的分隔）才解析出一个完整事件。流结束时不完整的事件会被丢弃。
 */
export async function* readSseEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const event = parseEventBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (event) yield event;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseEventBlock(block: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    if (field === "data") dataLines.push(value);
  }

  return dataLines.length > 0 ? { event, data: dataLines.join("\n") } : null;
}
