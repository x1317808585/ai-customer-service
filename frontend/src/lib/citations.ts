/**
 * 回复引用的知识库原文，以及正文里引用标记的显示方式。
 *
 * 后端保存引用时已经核对过：回复里写的是“「原文」[来源：来源标识]”，并且原文和标识都对得上。
 * 显示时只把这样完整对得上的标记换成编号 [1]、[2]，编号就是引用在列表里的顺序（也是它在正文里第一次出现的顺序）。
 * 同一个来源标识出现在别处、但前面不是逐字的原文时，不会被编上号。
 */

/** 回复引用的一段知识库原文（保存回复时的快照）和它的出处。 */
export type Citation = {
  source_id: string;
  document_title: string;
  section: string;
  content: string;
};

/** 回复正文的一段：普通文字，或者一个引用编号（从 1 开始）。 */
export type ReplyPart = { kind: "text"; text: string } | { kind: "citation"; number: number };

/** 把正文里已核对的引用标记换成编号，其余文字原样保留。没有引用时整段都是文字。 */
export function splitCitations(content: string, citations: Citation[]): ReplyPart[] {
  const marks: { start: number; end: number; number: number }[] = [];
  citations.forEach((citation, index) => {
    const quoted = `「${citation.content}」`;
    const mark = `[来源：${citation.source_id}]`;
    let from = 0;
    for (;;) {
      const at = content.indexOf(quoted + mark, from);
      if (at < 0) break;
      const start = at + quoted.length;
      marks.push({ start, end: start + mark.length, number: index + 1 });
      from = start + mark.length;
    }
  });
  marks.sort((a, b) => a.start - b.start);

  const parts: ReplyPart[] = [];
  let cursor = 0;
  for (const { start, end, number } of marks) {
    if (start > cursor) parts.push({ kind: "text", text: content.slice(cursor, start) });
    parts.push({ kind: "citation", number });
    cursor = end;
  }
  if (cursor < content.length) parts.push({ kind: "text", text: content.slice(cursor) });
  return parts;
}
