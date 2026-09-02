import { Button } from '@/components/ui/button';
import message from '@/components/ui/message';
import { DocumentApiAction } from '@/hooks/use-document-request';
import {
  IDocumentInfo,
  ITextbookKgMetadata,
} from '@/interfaces/database/document';
import {
  cancelTextbookKgJob,
  downloadTextbookKgBundle,
  fetchTextbookKgJob,
  importTextbookKgToGraphRag,
  retryTextbookKgJob,
} from '@/services/knowledge-service';
import { downloadFileFromBlob } from '@/utils/file-util';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, ListTree, Network, RotateCcw, X } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'umi';
import { TextbookChapterTreeDialog } from './textbook-chapter-tree-dialog';

const terminalStatuses = new Set(['succeeded', 'failed', 'canceled']);

export function TextbookKgStatusCell({ record }: { record: IDocumentInfo }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [treeOpen, setTreeOpen] = useState(false);
  const queryClient = useQueryClient();
  const initial = record.meta_fields?.textbook_kg;
  const queryKey = [DocumentApiAction.FetchTextbookKgJob, record.id];
  const { data = initial, refetch } = useQuery<ITextbookKgMetadata | undefined>(
    {
      queryKey,
      initialData: initial,
      enabled: Boolean(initial?.job_id),
      queryFn: async () => {
        const response = await fetchTextbookKgJob(record.id);
        return response?.data?.data as ITextbookKgMetadata;
      },
      refetchInterval: (query) => {
        const status = query.state.data?.status;
        return status && terminalStatuses.has(status) ? false : 5000;
      },
    },
  );

  const action = useMutation({
    mutationFn: async (name: 'retry' | 'cancel' | 'import') => {
      const response =
        name === 'retry'
          ? await retryTextbookKgJob(record.id)
          : name === 'import'
            ? await importTextbookKgToGraphRag(record.id)
            : await cancelTextbookKgJob(record.id);
      return response?.data?.data as ITextbookKgMetadata;
    },
    onSuccess: (next) => {
      queryClient.setQueryData(queryKey, next);
      queryClient.invalidateQueries({ queryKey: ['fetchKnowledgeGraph'] });
      refetch();
    },
  });

  if (!data?.job_id) {
    return <span className="text-text-secondary">—</span>;
  }

  const download = async () => {
    try {
      const response = await downloadTextbookKgBundle(record.id);
      const blob = new Blob([response.data], { type: 'application/zip' });
      downloadFileFromBlob(blob, `${record.name}-textbook-kg.zip`);
    } catch {
      message.error(t('fileManager.textbookKgDownloadFailed'));
    }
  };

  const label = t(`fileManager.textbookKgStatus.${data.status}`);
  const progress = Math.round((data.progress ?? 0) * 100);
  const graphStatus = data.graphrag?.status;
  const graphLabel = graphStatus
    ? t(`fileManager.textbookKgGraphRagStatus.${graphStatus}`)
    : undefined;

  return (
    <>
      <div className="flex min-w-36 items-center gap-1.5 text-xs">
        <span
          className={
            data.status === 'succeeded'
              ? 'text-green-600'
              : data.status === 'failed'
                ? 'text-red-600'
                : 'text-text-secondary'
          }
          title={data.error || data.stage || label}
        >
          {label}
          {!terminalStatuses.has(data.status) ? ` ${progress}%` : ''}
          {data.status === 'succeeded' && graphLabel ? ` · ${graphLabel}` : ''}
        </span>
        {data.status === 'succeeded' && (
          <>
            <span
              className="text-text-secondary"
              title={t('fileManager.textbookKgCounts')}
            >
              {data.result?.entity_count ?? 0}/
              {data.result?.relation_count ?? 0}/{data.result?.chunk_count ?? 0}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              title={t('fileManager.textbookKgDownload')}
              onClick={download}
            >
              <Download className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              title={t('fileManager.textbookChapterTree')}
              onClick={() => setTreeOpen(true)}
            >
              <ListTree className="size-3.5" />
            </Button>
            {graphStatus === 'imported' && (
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                title={t('fileManager.textbookKgViewGraph')}
                onClick={() =>
                  navigate(`/dataset/knowledge-graph/${record.kb_id}`)
                }
              >
                <Network className="size-3.5" />
              </Button>
            )}
            {graphStatus === 'failed' && (
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                title={
                  data.graphrag?.error || t('fileManager.textbookKgImportRetry')
                }
                disabled={action.isPending}
                onClick={() => action.mutate('import')}
              >
                <RotateCcw className="size-3.5" />
              </Button>
            )}
          </>
        )}
        {(data.status === 'failed' || data.status === 'canceled') && (
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            title={t('fileManager.textbookKgRetry')}
            disabled={action.isPending}
            onClick={() => action.mutate('retry')}
          >
            <RotateCcw className="size-3.5" />
          </Button>
        )}
        {(data.status === 'queued' || data.status === 'running') && (
          <Button
            variant="ghost"
            size="icon"
            className="size-6"
            title={t('fileManager.textbookKgCancel')}
            disabled={action.isPending}
            onClick={() => action.mutate('cancel')}
          >
            <X className="size-3.5" />
          </Button>
        )}
      </div>
      <TextbookChapterTreeDialog
        documentId={record.id}
        documentName={record.name}
        open={treeOpen}
        onOpenChange={setTreeOpen}
      />
    </>
  );
}
