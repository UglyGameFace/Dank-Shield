-- Keep Open Dank Cinema playback distinct from Discord Private Sessions and Watch Parties.
-- This migration is intentionally additive so a deployment that already applied the
-- Library Intelligence migration can safely accept the new website-only mode.

do $migration$
begin
    if to_regclass('public.dank_cinema_watch_sessions') is null then
        raise exception 'dank_cinema_watch_sessions must exist before standalone playback migration';
    end if;

    alter table public.dank_cinema_watch_sessions
        drop constraint if exists dank_cinema_watch_sessions_mode_check;

    alter table public.dank_cinema_watch_sessions
        add constraint dank_cinema_watch_sessions_mode_check
        check (session_mode in ('private', 'watch_party', 'standalone'));
end;
$migration$;

comment on column public.dank_cinema_watch_sessions.session_mode is
    'Playback surface: private, watch_party, or standalone Open Dank Cinema.';
