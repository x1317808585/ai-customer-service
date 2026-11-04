import type { Citation } from "@/lib/citations";

/**
 * 回复下方可折叠的“参考资料”：编号、出处（文档标题和小节）和原文，编号和正文里的 [1]、[2] 对应。
 * 默认收起。顾客聊天页在用；管理端查看聊天记录时也可以直接用它。
 */
export function CitationList({ citations }: { citations: Citation[] }) {
  return (
    <details className="group mt-2 border-t pt-2 text-sm">
      <summary className="cursor-pointer text-[13px] text-muted-foreground select-none hover:text-foreground">
        参考资料（{citations.length}）
      </summary>
      <ol aria-label="参考资料" className="mt-2 space-y-2">
        {citations.map((citation, index) => (
          <li key={citation.source_id} className="rounded-lg bg-card px-3 py-2">
            <p className="text-[13px] font-medium">
              [{index + 1}] 《{citation.document_title}》· {citation.section}
            </p>
            <p className="mt-1 text-[13px] whitespace-pre-wrap text-muted-foreground [overflow-wrap:anywhere]">
              {citation.content}
            </p>
          </li>
        ))}
      </ol>
    </details>
  );
}
