export interface IDailyActiveItem {
  date: string;
  dau: number;
}

export interface ITopActiveUser {
  user_id: string;
  nickname: string;
  email: string;
  active_days: number;
  last_seen_at: string;
}

export interface IModelUsageByModel {
  factory: string;
  llm_name: string;
  model_type: string;
  used_tokens: number;
  tenants: number;
}

export interface IModelUsageByFactory {
  factory: string;
  used_tokens: number;
  tenants: number;
}

export interface IModelUsageByType {
  model_type: string;
  used_tokens: number;
}

export interface IModelUsage {
  by_model: IModelUsageByModel[];
  by_factory: IModelUsageByFactory[];
  by_type: IModelUsageByType[];
  total_tokens: number;
}

export interface IDashboardStats {
  window_days: number;
  total_users: number;
  active_users: number;
  daily_active: IDailyActiveItem[];
  top_active_users: ITopActiveUser[];
  model_usage: IModelUsage;
}
