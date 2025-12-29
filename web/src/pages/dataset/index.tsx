import { PageHeader } from '@/components/page-header';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb';
import { useNavigatePage } from '@/hooks/logic-hooks/navigate-hooks';
import { useFetchKnowledgeBaseConfiguration } from '@/hooks/use-knowledge-request';
import { useFetchUserInfo } from '@/hooks/user-setting-hooks';
import { useTranslation } from 'react-i18next';
import { Outlet } from 'umi';
import { SideBar } from './sidebar';

export default function DatasetWrapper() {
  const { navigateToDatasetList } = useNavigatePage();
  const { t } = useTranslation();
  const { data } = useFetchKnowledgeBaseConfiguration();
  const { data: userInfo } = useFetchUserInfo();
  const isReadOnly =
    !!data?.tenant_id && !!userInfo?.id && data.tenant_id !== userInfo.id;

  return (
    <section className="flex h-full flex-col w-full">
      <PageHeader>
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink onClick={navigateToDatasetList}>
                {t('knowledgeDetails.dataset')}
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage className="w-28 whitespace-nowrap text-ellipsis overflow-hidden">
                {data.name}
              </BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
      </PageHeader>
      {isReadOnly && (
        <div className="mx-5 mt-3 rounded-md border border-border/60 bg-bg-card px-3 py-2 text-xs text-text-secondary">
          Read-only: only the owner can edit this knowledge base.
        </div>
      )}
      <div className="flex flex-1 min-h-0">
        <SideBar></SideBar>
        <div className="flex-1 overflow-auto">
          <Outlet />
        </div>
      </div>
    </section>
  );
}
