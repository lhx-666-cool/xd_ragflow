import { ConfirmDeleteDialog } from '@/components/confirm-delete-dialog';
import { Button } from '@/components/ui/button';
import {
  useFetchKnowledgeBaseConfiguration,
  useFetchKnowledgeGraph,
} from '@/hooks/use-knowledge-request';
import { useFetchUserInfo } from '@/hooks/user-setting-hooks';
import { Trash2 } from 'lucide-react';
import React from 'react';
import { useTranslation } from 'react-i18next';
import ForceGraph from './force-graph';
import { useDeleteKnowledgeGraph } from './use-delete-graph';

const KnowledgeGraph: React.FC = () => {
  const { data } = useFetchKnowledgeGraph();
  const { data: knowledgeDetails } = useFetchKnowledgeBaseConfiguration();
  const { data: userInfo } = useFetchUserInfo();
  const { t } = useTranslation();
  const { handleDeleteKnowledgeGraph } = useDeleteKnowledgeGraph();
  const isReadOnly =
    !!knowledgeDetails?.tenant_id &&
    !!userInfo?.id &&
    knowledgeDetails.tenant_id !== userInfo.id;
  const deleteButton = (
    <Button
      variant="outline"
      size={'sm'}
      className="absolute right-0 top-0 z-50"
      disabled={isReadOnly}
    >
      <Trash2 /> {t('common.delete')}
    </Button>
  );

  return (
    <section className={'w-full h-[90dvh] relative p-6'}>
      {isReadOnly ? (
        deleteButton
      ) : (
        <ConfirmDeleteDialog onOk={handleDeleteKnowledgeGraph}>
          {deleteButton}
        </ConfirmDeleteDialog>
      )}
      <ForceGraph data={data?.graph} show></ForceGraph>
    </section>
  );
};

export default KnowledgeGraph;
