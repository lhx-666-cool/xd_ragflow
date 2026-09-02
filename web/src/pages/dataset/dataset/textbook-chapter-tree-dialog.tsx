import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  ITextbookChapterTree,
  ITextbookChapterTreeNode,
} from '@/interfaces/database/document';
import { cn } from '@/lib/utils';
import { fetchTextbookChapterTree } from '@/services/knowledge-service';
import { useQuery } from '@tanstack/react-query';
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  FileText,
  Loader2,
  RefreshCcw,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

type TextbookChapterTreeDialogProps = {
  documentId: string;
  documentName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

function collectExpandableIds(nodes: ITextbookChapterTreeNode[]) {
  const result: string[] = [];
  const visit = (items: ITextbookChapterTreeNode[]) => {
    items.forEach((item) => {
      if (item.children.length) result.push(item.id);
      visit(item.children);
    });
  };
  visit(nodes);
  return result;
}

function findNode(nodes: ITextbookChapterTreeNode[], id?: string) {
  if (!id) return undefined;
  for (const node of nodes) {
    if (node.id === id) return node;
    const child = findNode(node.children, id);
    if (child) return child;
  }
}

function pageRange(start?: number | null, end?: number | null) {
  if (start === null || start === undefined) return '—';
  return end !== null && end !== undefined && end !== start
    ? `${start}–${end}`
    : String(start);
}

function TreeRows({
  nodes,
  depth,
  expanded,
  selectedId,
  onSelect,
}: {
  nodes: ITextbookChapterTreeNode[];
  depth: number;
  expanded: Set<string>;
  selectedId?: string;
  onSelect: (node: ITextbookChapterTreeNode) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0;
        const isExpanded = expanded.has(node.id);
        return (
          <div key={node.id}>
            <button
              type="button"
              className={cn(
                'group flex w-full items-center gap-2 border-l-2 py-2 pr-3 text-left transition-colors',
                selectedId === node.id
                  ? 'border-primary bg-primary/10 text-text-primary'
                  : 'border-transparent text-text-secondary hover:bg-bg-card hover:text-text-primary',
              )}
              style={{ paddingLeft: `${12 + depth * 20}px` }}
              onClick={() => onSelect(node)}
            >
              <span className="flex size-5 shrink-0 items-center justify-center">
                {hasChildren ? (
                  isExpanded ? (
                    <ChevronDown className="size-4" />
                  ) : (
                    <ChevronRight className="size-4" />
                  )
                ) : (
                  <span className="size-1.5 rounded-full bg-text-disabled" />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {node.label}
              </span>
              {node.pdf_page_start !== null &&
                node.pdf_page_start !== undefined && (
                  <span className="shrink-0 rounded-full bg-bg-card px-2 py-0.5 text-[11px] text-text-secondary">
                    PDF {pageRange(node.pdf_page_start, node.pdf_page_end)}
                  </span>
                )}
            </button>
            {hasChildren && isExpanded && (
              <TreeRows
                nodes={node.children}
                depth={depth + 1}
                expanded={expanded}
                selectedId={selectedId}
                onSelect={onSelect}
              />
            )}
          </div>
        );
      })}
    </>
  );
}

export function TextbookChapterTreeDialog({
  documentId,
  documentName,
  open,
  onOpenChange,
}: TextbookChapterTreeDialogProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string>();
  const { data, isLoading, isError, refetch } = useQuery<
    ITextbookChapterTree | undefined
  >({
    queryKey: ['fetchTextbookChapterTree', documentId],
    enabled: open,
    queryFn: async () => {
      const response = await fetchTextbookChapterTree(documentId);
      return response?.data?.data as ITextbookChapterTree;
    },
    staleTime: 5 * 60 * 1000,
  });
  const expandableIds = useMemo(
    () => collectExpandableIds(data?.chapters ?? []),
    [data],
  );
  const selected = useMemo(
    () => findNode(data?.chapters ?? [], selectedId),
    [data, selectedId],
  );

  useEffect(() => {
    if (!data?.chapters.length) return;
    setExpanded(new Set(data.chapters.map((chapter) => chapter.id)));
    setSelectedId((current) => current ?? data.chapters[0].id);
  }, [data]);

  const selectNode = (node: ITextbookChapterTreeNode) => {
    setSelectedId(node.id);
    if (!node.children.length) return;
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[82vh] max-w-6xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-5">
          <DialogTitle className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <BookOpen className="size-5" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-lg">
                {data?.book_title || documentName}
              </span>
              <span className="mt-0.5 block text-xs font-normal text-text-secondary">
                {t('fileManager.textbookChapterTree')}
              </span>
            </span>
          </DialogTitle>
        </DialogHeader>

        {isLoading && (
          <div className="flex flex-1 items-center justify-center gap-3 text-text-secondary">
            <Loader2 className="size-5 animate-spin" />
            {t('fileManager.textbookChapterTreeLoading')}
          </div>
        )}
        {isError && (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 text-text-secondary">
            <RefreshCcw className="size-8 text-text-disabled" />
            <span>{t('fileManager.textbookChapterTreeLoadFailed')}</span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              {t('fileManager.textbookKgRetry')}
            </Button>
          </div>
        )}
        {data && !isLoading && !isError && (
          <div className="flex min-h-0 flex-1 bg-bg-base">
            <section className="flex w-[46%] min-w-0 flex-col border-r bg-bg-base">
              <div className="flex items-center justify-between border-b px-4 py-3">
                <div className="text-xs text-text-secondary">
                  {t('fileManager.textbookChapterTreeSummary', {
                    nodes: data.node_count,
                    depth: data.max_depth,
                  })}
                </div>
                <div className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    title={t('fileManager.textbookChapterTreeExpandAll')}
                    onClick={() => setExpanded(new Set(expandableIds))}
                  >
                    <ChevronsUpDown className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    title={t('fileManager.textbookChapterTreeCollapseAll')}
                    onClick={() => setExpanded(new Set())}
                  >
                    <ChevronsDownUp className="size-4" />
                  </Button>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto py-2">
                <TreeRows
                  nodes={data.chapters}
                  depth={0}
                  expanded={expanded}
                  selectedId={selected?.id}
                  onSelect={selectNode}
                />
              </div>
            </section>

            <section className="min-w-0 flex-1 overflow-y-auto p-7">
              {selected ? (
                <div className="mx-auto max-w-xl">
                  <div className="mb-7 flex items-start gap-4">
                    <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-sm">
                      <FileText className="size-5" />
                    </span>
                    <div className="min-w-0">
                      <div className="text-xs font-semibold uppercase tracking-wide text-primary">
                        {t('fileManager.textbookChapterTreeLevel', {
                          level: selected.level,
                        })}
                      </div>
                      <h3 className="mt-1 text-xl font-semibold text-text-primary">
                        {selected.label}
                      </h3>
                    </div>
                  </div>
                  <dl className="grid grid-cols-3 gap-3">
                    <div className="rounded-xl border bg-bg-card p-4">
                      <dt className="text-xs text-text-secondary">
                        {t('fileManager.textbookChapterTreePdfPages')}
                      </dt>
                      <dd className="mt-2 text-lg font-semibold">
                        {pageRange(
                          selected.pdf_page_start,
                          selected.pdf_page_end,
                        )}
                      </dd>
                    </div>
                    <div className="rounded-xl border bg-bg-card p-4">
                      <dt className="text-xs text-text-secondary">
                        {t('fileManager.textbookChapterTreeTocPages')}
                      </dt>
                      <dd className="mt-2 text-lg font-semibold">
                        {pageRange(
                          selected.toc_page_start,
                          selected.toc_page_end,
                        )}
                      </dd>
                    </div>
                    <div className="rounded-xl border bg-bg-card p-4">
                      <dt className="text-xs text-text-secondary">
                        {t('fileManager.textbookChapterTreeChildren')}
                      </dt>
                      <dd className="mt-2 text-lg font-semibold">
                        {selected.child_count}
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-7">
                    <div className="mb-3 text-sm font-semibold text-text-primary">
                      {t('fileManager.textbookChapterTreeContentPreview')}
                    </div>
                    <div className="min-h-32 whitespace-pre-wrap rounded-xl border bg-bg-card p-5 text-sm leading-7 text-text-secondary">
                      {selected.content_preview ||
                        t('fileManager.textbookChapterTreeNoContent')}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex h-full items-center justify-center text-text-secondary">
                  {t('fileManager.textbookChapterTreeEmpty')}
                </div>
              )}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
