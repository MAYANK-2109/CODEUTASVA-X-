-- ============================================================
-- Supabase SQL Migration: portfolio_holdings table
-- Run this in the Supabase SQL Editor (Settings → SQL Editor).
-- ============================================================

-- 1. Create the table
create table if not exists portfolio_holdings (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid references auth.users(id) on delete cascade not null,
  name          text not null,
  symbol        text not null default '',
  isin          text not null default '',
  type          text not null default 'STOCK',
  buy_date      date,
  units         numeric(18, 6),
  buy_price     numeric(18, 2),
  current_price numeric(18, 2),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- 2. Index for fast per-user queries
create index if not exists idx_portfolio_holdings_user_id
  on portfolio_holdings(user_id);

-- 3. Row Level Security — users can only CRUD their own rows
alter table portfolio_holdings enable row level security;

-- Drop old policy if it exists, then recreate
drop policy if exists "Users own their holdings" on portfolio_holdings;
create policy "Users own their holdings"
  on portfolio_holdings
  for all
  using  (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- 4. Auto-update the updated_at column
create or replace function update_updated_at_column()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_portfolio_holdings_updated_at on portfolio_holdings;
create trigger trg_portfolio_holdings_updated_at
  before update on portfolio_holdings
  for each row execute procedure update_updated_at_column();
