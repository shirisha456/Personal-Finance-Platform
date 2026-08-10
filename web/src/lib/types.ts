export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiError {
  error: {
    type: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface User {
  id: string;
  email: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface WsTicketResponse {
  ticket: string;
  expires_in_seconds: number;
}

export type AccountType = "checking" | "savings" | "credit" | "investment" | "loan" | "cash";

export interface Account {
  id: string;
  name: string;
  type: AccountType;
  currency: string;
  current_balance_minor: number;
  created_at: string;
}

export interface Category {
  id: string;
  name: string;
  parent_id: string | null;
  is_system: boolean;
  created_at: string;
}

export interface Transaction {
  id: string;
  account_id: string;
  category_id: string | null;
  merchant_name: string;
  description: string | null;
  amount_minor: number;
  currency: string;
  txn_date: string;
  pending: boolean;
  external_id: string | null;
  created_at: string;
}

export type InstitutionStatus = "active" | "error" | "revoked";

export interface Institution {
  id: string;
  name: string;
  plaid_institution_id: string | null;
  status: InstitutionStatus;
  created_at: string;
}

export interface Budget {
  id: string;
  category_id: string;
  month: string;
  amount_minor: number;
  created_at: string;
}

export interface BudgetActual {
  category_id: string;
  category_name: string;
  budgeted_minor: number;
  actual_minor: number;
  remaining_minor: number;
}

export interface Goal {
  id: string;
  name: string;
  target_amount_minor: number;
  current_amount_minor: number;
  target_date: string | null;
  created_at: string;
}

export interface Security {
  id: string;
  symbol: string;
  name: string;
  latest_price_minor: number | null;
  latest_price_at: string | null;
}

export interface SymbolSearchResult {
  symbol: string;
  name: string;
  exchange: string;
}

export interface Holding {
  id: string;
  account_id: string;
  security: Security;
  quantity: number;
  cost_basis_minor: number;
  created_at: string;
}

export interface WatchlistItem {
  id: string;
  security: Security;
  created_at: string;
}

export interface NetWorthSnapshot {
  id: string;
  snapshot_date: string;
  assets_minor: number;
  liabilities_minor: number;
  net_worth_minor: number;
  created_at: string;
}

export interface RecurringItem {
  merchant_name: string;
  category_name: string | null;
  average_amount_minor: number;
  occurrences: number;
  average_interval_days: number;
  last_seen: string;
  next_expected_date: string;
}

export interface ForecastPoint {
  date: string;
  projected_balance_minor: number;
}

export interface ForecastResponse {
  as_of: string;
  horizon_days: number;
  starting_balance_minor: number;
  projected_ending_balance_minor: number;
  recurring_items: RecurringItem[];
  daily_projection: ForecastPoint[];
}

export type AlertType = "duplicate_charge" | "spend_spike" | "subscription_price_increase";
export type AlertSeverity = "info" | "warning" | "critical";

export interface Alert {
  id: string;
  alert_type: AlertType;
  severity: AlertSeverity;
  title: string;
  detail: string;
  related_transaction_id: string | null;
  read_at: string | null;
  created_at: string;
}

export interface Insight {
  id: string;
  period_start: string;
  period_end: string;
  summary: string;
  created_at: string;
}

export interface LiveNotification {
  type: "alert" | "insight";
  data: Record<string, unknown>;
}
