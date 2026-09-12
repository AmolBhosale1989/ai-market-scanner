-- Market Hunt commercial foundation schema
-- PostgreSQL-compatible; not yet connected to the personal-use scanner.

create table if not exists app_users (
  id uuid primary key,
  email text not null unique,
  created_at timestamptz not null default now(),
  status text not null default 'active'
);

create table if not exists subscriptions (
  id uuid primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  provider text,
  provider_customer_id text,
  provider_subscription_id text,
  plan_code text not null,
  status text not null,
  current_period_end timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists scan_runs (
  id bigserial primary key,
  generated_at timestamptz not null,
  source_commit text,
  market_regime text,
  master_symbols integer,
  tradable_symbols integer,
  analyzable_symbols integer,
  scan_status text not null,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists signals (
  id bigserial primary key,
  scan_run_id bigint references scan_runs(id) on delete set null,
  ticker text not null,
  signal_id text,
  stage text,
  decision text,
  setup_type text,
  market_hunt_score numeric,
  technical_score numeric,
  effective_rr numeric,
  entry_trigger numeric,
  stop numeric,
  effective_target numeric,
  market_regime text,
  theme text,
  catalyst_status text,
  payload jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now()
);

create index if not exists idx_signals_ticker_seen on signals(ticker, first_seen_at desc);
create index if not exists idx_signals_stage_seen on signals(stage, first_seen_at desc);

create table if not exists signal_outcomes (
  id bigserial primary key,
  signal_id bigint not null references signals(id) on delete cascade,
  triggered_at timestamptz,
  closed_at timestamptz,
  outcome text,
  return_pct numeric,
  r_multiple numeric,
  exit_price numeric,
  payload jsonb not null default '{}'::jsonb
);

create table if not exists alerts (
  id bigserial primary key,
  signal_id bigint references signals(id) on delete set null,
  user_id uuid references app_users(id) on delete cascade,
  channel text not null,
  state_from text,
  state_to text,
  delivered boolean not null default false,
  delivered_at timestamptz,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists user_watchlists (
  id bigserial primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  ticker text not null,
  created_at timestamptz not null default now(),
  unique(user_id,ticker)
);
