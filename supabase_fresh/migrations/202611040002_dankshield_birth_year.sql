-- Add a private birth year without deleting or rewriting existing birthday preferences.
-- Run after 202611040001_dankshield_native_birthdays.sql.
-- Existing yearless records stay valid until members update their birthday.
alter table public.dank_birthday_members
    add column if not exists birth_year smallint;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.dank_birthday_members'::regclass
          and conname = 'dank_birthday_valid_year'
    ) then
        alter table public.dank_birthday_members
            add constraint dank_birthday_valid_year
            check (
                birth_year is null
                or (birth_year between 1900 and 2100
                    and birth_day <= extract(day from (
                        make_date(birth_year, birth_month, 1) + interval '1 month - 1 day'
                    )))
            );
    end if;
end;
$$;

-- Existing RLS and service-role-only grants apply to the added column.
-- A self-reported birthday is never age-verification evidence.
