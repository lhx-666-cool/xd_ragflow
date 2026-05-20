import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useFetchDashboardStats } from '@/hooks/use-dashboard-stats';
import { useFetchUserInfo } from '@/hooks/user-setting-hooks';
import { Routes } from '@/routes';
import { LucideInfo, LucideRefreshCw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'umi';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const PIE_COLORS = [
  '#6366f1',
  '#22c55e',
  '#f97316',
  '#06b6d4',
  '#ec4899',
  '#a855f7',
  '#eab308',
  '#0ea5e9',
];

const formatTokens = (n: number) => {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
  return String(n);
};

const SettingDashboard = () => {
  const { t } = useTranslation();
  const { data: userInfo, loading: userLoading } = useFetchUserInfo();
  const [days, setDays] = useState<number>(7);
  const top = 10;
  const { data, loading, refetch } = useFetchDashboardStats(days, top);

  const factoryPie = useMemo(
    () =>
      (data.model_usage?.by_factory ?? []).map((it) => ({
        name: it.factory || 'unknown',
        value: it.used_tokens,
      })),
    [data],
  );

  if (!userLoading && !userInfo?.is_admin) {
    return <Navigate to={Routes.UserSetting + Routes.Profile} replace />;
  }

  return (
    <section className="flex flex-col h-full w-full p-6 overflow-auto gap-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t('dashboard.title')}</h1>
          <p className="text-sm text-text-secondary">
            {t('dashboard.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select
            value={String(days)}
            onValueChange={(v) => setDays(Number(v))}
          >
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1">{t('dashboard.range.day1')}</SelectItem>
              <SelectItem value="7">{t('dashboard.range.day7')}</SelectItem>
              <SelectItem value="14">{t('dashboard.range.day14')}</SelectItem>
              <SelectItem value="30">{t('dashboard.range.day30')}</SelectItem>
              <SelectItem value="90">{t('dashboard.range.day90')}</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="icon"
            onClick={() => refetch()}
            disabled={loading}
            title={t('dashboard.refresh')}
          >
            <LucideRefreshCw
              className={loading ? 'animate-spin size-4' : 'size-4'}
            />
          </Button>
        </div>
      </header>

      <section className="grid grid-cols-3 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-text-secondary">
              {t('dashboard.totalUsers')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold">{data.total_users}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-text-secondary">
              {t('dashboard.activeUsersInWindow', {
                days: data.window_days || days,
              })}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold">{data.active_users}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-text-secondary flex items-center gap-1">
              {t('dashboard.totalTokens')}
              <LucideInfo
                className="size-3.5"
                aria-label={t('dashboard.allTimeTokensNote') as string}
              />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-semibold">
              {formatTokens(data.model_usage?.total_tokens ?? 0)}
            </div>
            <p className="text-xs text-text-secondary mt-1">
              {t('dashboard.allTimeTokensNote')}
            </p>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>{t('dashboard.dauTrend')}</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.daily_active}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tickMargin={8} fontSize={12} />
              <YAxis allowDecimals={false} fontSize={12} />
              <Tooltip />
              <Legend />
              <Bar
                dataKey="dau"
                name={t('dashboard.dau')}
                fill="#6366f1"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <section className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>{t('dashboard.topActiveUsers')}</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('dashboard.user')}</TableHead>
                  <TableHead>{t('dashboard.email')}</TableHead>
                  <TableHead className="text-right">
                    {t('dashboard.activeDays')}
                  </TableHead>
                  <TableHead>{t('dashboard.lastSeenAt')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.top_active_users.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="text-center text-text-secondary py-8"
                    >
                      {t('dashboard.empty')}
                    </TableCell>
                  </TableRow>
                ) : (
                  data.top_active_users.map((u) => (
                    <TableRow key={u.user_id}>
                      <TableCell className="font-medium">
                        {u.nickname || '-'}
                      </TableCell>
                      <TableCell className="text-text-secondary">
                        {u.email}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {u.active_days}
                      </TableCell>
                      <TableCell className="text-text-secondary">
                        {u.last_seen_at}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1">
              {t('dashboard.tokensByFactory')}
              <LucideInfo
                className="size-3.5 text-text-secondary"
                aria-label={t('dashboard.allTimeTokensNote') as string}
              />
            </CardTitle>
          </CardHeader>
          <CardContent className="h-80">
            {factoryPie.length === 0 ? (
              <div className="flex items-center justify-center h-full text-text-secondary text-sm">
                {t('dashboard.empty')}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={factoryPie}
                    dataKey="value"
                    nameKey="name"
                    outerRadius={100}
                    label={(e: { name: string; percent?: number }) =>
                      `${e.name} ${((e.percent ?? 0) * 100).toFixed(1)}%`
                    }
                  >
                    {factoryPie.map((_, i) => (
                      <Cell
                        key={i}
                        fill={PIE_COLORS[i % PIE_COLORS.length]}
                      />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(v: number) => formatTokens(Number(v))}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1">
            {t('dashboard.topModels')}
            <LucideInfo
              className="size-3.5 text-text-secondary"
              aria-label={t('dashboard.allTimeTokensNote') as string}
            />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('dashboard.factory')}</TableHead>
                <TableHead>{t('dashboard.modelName')}</TableHead>
                <TableHead>{t('dashboard.modelType')}</TableHead>
                <TableHead className="text-right">
                  {t('dashboard.tenants')}
                </TableHead>
                <TableHead className="text-right">
                  {t('dashboard.usedTokens')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.model_usage.by_model.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={5}
                    className="text-center text-text-secondary py-8"
                  >
                    {t('dashboard.empty')}
                  </TableCell>
                </TableRow>
              ) : (
                data.model_usage.by_model.map((m, idx) => (
                  <TableRow key={`${m.factory}-${m.llm_name}-${idx}`}>
                    <TableCell className="font-medium">{m.factory}</TableCell>
                    <TableCell>{m.llm_name}</TableCell>
                    <TableCell className="text-text-secondary">
                      {m.model_type}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {m.tenants}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatTokens(m.used_tokens)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  );
};

export default SettingDashboard;
