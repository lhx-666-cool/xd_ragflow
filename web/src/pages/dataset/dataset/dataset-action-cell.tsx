import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button } from '@/components/ui/button';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card';
import { DocumentType } from '@/constants/knowledge';
import { useRemoveDocument } from '@/hooks/use-document-request';
import { IDocumentInfo } from '@/interfaces/database/document';
import { formatFileSize } from '@/utils/common-util';
import { Routes } from '@/routes';
import { formatDate } from '@/utils/date';
import { downloadDocument } from '@/utils/file-util';
import { getExtension } from '@/utils/document-util';
import { Download, Eye, MessageSquare, PenLine, Trash2 } from 'lucide-react';
import { useCallback, useMemo } from 'react';
import { useNavigate } from 'umi';
import { UseRenameDocumentShowType } from './use-rename-document';
import { isParserRunning } from './utils';

const Fields = ['name', 'size', 'type', 'create_time', 'update_time'];

const FunctionMap = {
  size: formatFileSize,
  create_time: formatDate,
  update_time: formatDate,
};

export function DatasetActionCell({
  record,
  showRenameModal,
}: { record: IDocumentInfo } & UseRenameDocumentShowType) {
  const { id, run, type } = record;
  const isRunning = isParserRunning(run);
  const isVirtualDocument = type === DocumentType.Virtual;
  const navigate = useNavigate();

  const { removeDocument } = useRemoveDocument();

  const extension = useMemo(() => {
    const suffix = (record.suffix || '').replace('.', '');
    return (suffix || getExtension(record.name)).toLowerCase();
  }, [record.name, record.suffix]);

  const onDownloadDocument = useCallback(() => {
    downloadDocument({
      id,
      filename: record.name,
    });
  }, [id, record.name]);

  const handleRemove = useCallback(() => {
    removeDocument(id);
  }, [id, removeDocument]);

  const handleRename = useCallback(() => {
    showRenameModal(record);
  }, [record, showRenameModal]);

  const handleOpenDocumentChat = useCallback(() => {
    if (!record.id) return;
    const params = new URLSearchParams({
      ext: extension || 'pdf',
      prefix: 'document',
      name: record.name,
    });
    navigate(`${Routes.DocumentChat}/${record.id}?${params.toString()}`);
  }, [extension, navigate, record.id, record.name]);

  return (
    <section className="flex gap-4 items-center text-text-sub-title-invert opacity-0 group-hover:opacity-100 transition-opacity">
      <Button
        variant="transparent"
        className="border-none hover:bg-bg-card text-text-primary"
        size={'sm'}
        disabled={isRunning}
        onClick={handleOpenDocumentChat}
      >
        <MessageSquare />
      </Button>
      <Button
        variant="transparent"
        className="border-none hover:bg-bg-card text-text-primary"
        size={'sm'}
        disabled={isRunning}
        onClick={handleRename}
      >
        <PenLine />
      </Button>
      <HoverCard>
        <HoverCardTrigger>
          <Button
            variant="transparent"
            className="border-none hover:bg-bg-card text-text-primary"
            disabled={isRunning}
            size={'sm'}
          >
            <Eye />
          </Button>
        </HoverCardTrigger>
        <HoverCardContent className="w-[40vw] max-h-[40vh] overflow-auto">
          <ul className="space-y-2">
            {Object.entries(record)
              .filter(([key]) => Fields.some((x) => x === key))

              .map(([key, value], idx) => {
                return (
                  <li key={idx} className="flex gap-2">
                    {key}:
                    <div>
                      {key in FunctionMap
                        ? FunctionMap[key as keyof typeof FunctionMap](value)
                        : value}
                    </div>
                  </li>
                );
              })}
          </ul>
        </HoverCardContent>
      </HoverCard>

      {isVirtualDocument || (
        <Button
          variant="transparent"
          className="border-none hover:bg-bg-card text-text-primary"
          onClick={onDownloadDocument}
          disabled={isRunning}
          size={'sm'}
        >
          <Download />
        </Button>
      )}
      <ConfirmDeleteDialog onOk={handleRemove}>
        <Button
          variant="transparent"
          className="border-none hover:bg-bg-card text-text-primary"
          size={'sm'}
          disabled={isRunning}
        >
          <Trash2 />
        </Button>
      </ConfirmDeleteDialog>
    </section>
  );
}
