create table if not exists public.verizon_reward_configs (
    guild_id text primary key,
    alert_channel_id text,
    enabled boolean not null default false,
    reminders_enabled boolean not null default true,
    reminder_offsets_minutes jsonb not null default '[30,10,1]'::jsonb,
    priority_keywords jsonb not null default '["gift card","daily drop","epic wins","presale","ticket","tickets","fifa","sweepstakes","merch","local pass"]'::jsonb,
    quiet_hours_start text,
    quiet_hours_end text,
    staff_only_commands boolean not null default true,
    updated_by text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.verizon_rewards (
    reward_id text not null,
    guild_id text not null,
    title text not null,
    type text not null default 'Unknown',
    status text not null default 'unknown',
    source text not null default 'manual',
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    available_at timestamptz,
    expires_at timestamptz,
    priority text not null default 'normal',
    raw_text text,
    fingerprint_hash text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, reward_id)
);

create table if not exists public.verizon_reward_reminders (
    guild_id text not null,
    reward_id text not null,
    offset_minutes integer not null,
    remind_at timestamptz not null,
    sent boolean not null default false,
    sent_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, reward_id, offset_minutes)
);

alter table public.verizon_reward_configs add column if not exists alert_channel_id text;
alter table public.verizon_reward_configs add column if not exists enabled boolean not null default false;
alter table public.verizon_reward_configs add column if not exists reminders_enabled boolean not null default true;
alter table public.verizon_reward_configs add column if not exists reminder_offsets_minutes jsonb not null default '[30,10,1]'::jsonb;
alter table public.verizon_reward_configs add column if not exists priority_keywords jsonb not null default '[]'::jsonb;
alter table public.verizon_reward_configs add column if not exists quiet_hours_start text;
alter table public.verizon_reward_configs add column if not exists quiet_hours_end text;
alter table public.verizon_reward_configs add column if not exists staff_only_commands boolean not null default true;
alter table public.verizon_reward_configs add column if not exists updated_by text;
alter table public.verizon_reward_configs add column if not exists updated_at timestamptz not null default now();

alter table public.verizon_rewards add column if not exists type text not null default 'Unknown';
alter table public.verizon_rewards add column if not exists status text not null default 'unknown';
alter table public.verizon_rewards add column if not exists source text not null default 'manual';
alter table public.verizon_rewards add column if not exists first_seen_at timestamptz not null default now();
alter table public.verizon_rewards add column if not exists last_seen_at timestamptz not null default now();
alter table public.verizon_rewards add column if not exists available_at timestamptz;
alter table public.verizon_rewards add column if not exists expires_at timestamptz;
alter table public.verizon_rewards add column if not exists priority text not null default 'normal';
alter table public.verizon_rewards add column if not exists raw_text text;
alter table public.verizon_rewards add column if not exists fingerprint_hash text not null default '';
alter table public.verizon_rewards add column if not exists metadata jsonb not null default '{}'::jsonb;
alter table public.verizon_rewards add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_verizon_rewards_guild_last_seen on public.verizon_rewards (guild_id, last_seen_at desc);
create index if not exists idx_verizon_rewards_fingerprint on public.verizon_rewards (guild_id, fingerprint_hash);
create index if not exists idx_verizon_reward_reminders_due on public.verizon_reward_reminders (sent, remind_at);
