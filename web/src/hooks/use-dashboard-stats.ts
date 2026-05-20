import message from '@/components/ui/message';
import { IDashboardStats } from '@/interfaces/database/dashboard';
import userService from '@/services/user-service';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useFetchUserInfo } from './user-setting-hooks';

const EMPTY: IDashboardStats = {
  window_days: 0,
  total_users: 0,
  active_users: 0,
  daily_active: [],
  top_active_users: [],
  model_usage: { by_model: [], by_factory: [], by_type: [], total_tokens: 0 },
};

export const useFetchDashboardStats = (days: number, top: number) => {
  const { t } = useTranslation();
  const { data: userInfo } = useFetchUserInfo();
  const enabled = !!userInfo?.is_admin;

  const { data, isFetching, refetch } = useQuery<IDashboardStats>({
    queryKey: ['dashboardStats', days, top],
    enabled,
    initialData: EMPTY,
    // Cheap to keep around: a single small JSON. Holding it for 30s avoids
    // re-fetching every time the admin tabs into Dashboard.
    gcTime: 30_000,
    staleTime: 30_000,
    queryFn: async () => {
      const { data } = await userService.dashboardStats({ days, top });
      if (data?.code === 0) {
        return data.data as IDashboardStats;
      }
      message.error(data?.message || t('dashboard.loadFailed'));
      return EMPTY;
    },
  });

  return { data, loading: isFetching, refetch };
};
