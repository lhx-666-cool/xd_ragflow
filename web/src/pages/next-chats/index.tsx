import { CardContainer } from '@/components/card-container';
import { HomeCard } from '@/components/home-card';
import ListFilterBar from '@/components/list-filter-bar';
import { RenameDialog } from '@/components/rename-dialog';
import { Button } from '@/components/ui/button';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { AgentCategory } from '@/constants/agent';
import { SharedFrom } from '@/constants/chat';
import { useFetchDialogList } from '@/hooks/use-chat-request';
import { useFetchAgentList } from '@/hooks/use-agent-request';
import { useFetchSystemTokenList } from '@/hooks/user-setting-hooks';
import { Routes } from '@/routes';
import { pick } from 'lodash';
import { Plus } from 'lucide-react';
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ChatCard } from './chat-card';
import { useRenameChat } from './hooks/use-rename-chat';

export default function ChatList() {
  const { data, setPagination, pagination, handleInputChange, searchString } =
    useFetchDialogList();
  const { t } = useTranslation();
  const {
    initialChatName,
    chatRenameVisible,
    showChatRenameModal,
    hideChatRenameModal,
    onChatRenameOk,
    chatRenameLoading,
  } = useRenameChat();
  const { data: agentList } = useFetchAgentList({
    canvas_category: AgentCategory.AgentCanvas,
  });
  const { data: tokenList } = useFetchSystemTokenList();
  const beta = tokenList?.[0]?.beta ?? '';

  const handlePageChange = useCallback(
    (page: number, pageSize?: number) => {
      setPagination({ page, pageSize });
    },
    [setPagination],
  );

  const handleShowCreateModal = useCallback(() => {
    showChatRenameModal();
  }, [showChatRenameModal]);

  const buildAgentShareUrl = useCallback(
    (agentId: string) => {
      const baseUrl = `${location.origin}${Routes.AgentShare}?shared_id=${agentId}&from=${SharedFrom.Agent}`;
      return beta ? `${baseUrl}&auth=${beta}` : baseUrl;
    },
    [beta],
  );

  const handleOpenAgentShare = useCallback(
    (agentId: string) => {
      if (typeof window === 'undefined') {
        return;
      }
      window.location.assign(buildAgentShareUrl(agentId));
    },
    [buildAgentShareUrl],
  );

  return (
    <section className="flex flex-col w-full flex-1">
      <div className="px-8 pt-8">
        <ListFilterBar
          title={t('chat.chatApps')}
          icon="chats"
          onSearchChange={handleInputChange}
          searchString={searchString}
        >
          <Button onClick={handleShowCreateModal}>
            <Plus className="size-2.5" />
            {t('chat.createChat')}
          </Button>
        </ListFilterBar>
      </div>
      <div className="flex-1 overflow-auto">
        <CardContainer className="max-h-[calc(100dvh-280px)] overflow-auto px-8">
          {agentList.canvas.map((agent) => (
            <HomeCard
              key={`agent-${agent.id}`}
              data={{
                name: agent.title,
                description: agent.description,
                avatar: agent.avatar,
                update_time: agent.update_time,
              }}
              moreDropdown={null}
              onClick={() => handleOpenAgentShare(agent.id)}
            />
          ))}
          {data.dialogs.map((x) => {
            return (
              <ChatCard
                key={x.id}
                data={x}
                showChatRenameModal={showChatRenameModal}
              ></ChatCard>
            );
          })}
        </CardContainer>
      </div>
      <div className="mt-8 px-8 pb-8">
        <RAGFlowPagination
          {...pick(pagination, 'current', 'pageSize')}
          total={pagination.total}
          onChange={handlePageChange}
        ></RAGFlowPagination>
      </div>
      {chatRenameVisible && (
        <RenameDialog
          hideModal={hideChatRenameModal}
          onOk={onChatRenameOk}
          initialName={initialChatName}
          loading={chatRenameLoading}
          title={initialChatName || t('chat.createChat')}
        ></RenameDialog>
      )}
    </section>
  );
}
