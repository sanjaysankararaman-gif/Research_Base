-- Run once in the Supabase SQL editor. Re-running is safe.
--
-- If you ran the earlier version of this file (faculty keyed by a generated uuid), drop the
-- old faculty tables first, then run this file:
--   drop view if exists home_institution_publications;
--   drop table if exists publication_faculty, faculty_name_variants, faculty cascade;
--   alter table publications add column if not exists home_institution text;

create table if not exists publications (
  id                bigint generated always as identity primary key,
  doi               text not null unique check (doi = lower(doi)),
  publication_title text not null,
  publication_type  text not null check (publication_type in ('Journal', 'Conference')),
  authors           text[] not null,
  publication_year  int,
  publication_date  date,                 -- full date when known (YYYY-MM-DD)
  publisher         text,
  publication_url   text,
  indexing_database text[],
  home_institution  text,                 -- e.g. 'Shiv Nadar University, Chennai'; null if no home author
  created_at        timestamptz not null default now()
);

create table if not exists journal_details (
  publication_id bigint primary key references publications(id) on delete cascade,
  journal_name   text not null,
  issn           text,
  e_issn         text,
  volume         text,
  issue          text,
  page_range     text,
  quartile       text check (quartile in ('Q1', 'Q2', 'Q3', 'Q4', 'N/A')),
  impact_factor  numeric(6,3)
);

create table if not exists conference_details (
  publication_id        bigint primary key references publications(id) on delete cascade,
  conference_name       text,
  conference_acronym    text,
  conference_start      date,
  conference_end        date,
  conference_location   text,
  proceedings_title     text,
  proceedings_publisher text,
  proceedings_isbn      text,
  proceedings_issn      text,
  proceedings_volume    text,
  proceedings_pages     text
);

-- Home-institution faculty. Filled from reference/faculty.csv on every write, so the
-- faculty_id here is the same ID the app and the Excel file show. Edit the CSV, not this table.
create table if not exists faculty (
  faculty_id    text primary key,         -- e.g. 'SNUC-F0001'; fixed, never reused
  full_name     text not null unique,     -- display name, e.g. 'K.S. Gayathri'
  given_names   text not null,            -- e.g. 'sreehari', or 'k s' for initials
  family_name   text not null,            -- surname; a single letter if only the initial is known
  name_variants text[],                   -- spellings the matching rules cannot reach
  created_at    timestamptz not null default now()
);

-- Which home-institution teachers wrote which publication.
create table if not exists publication_faculty (
  publication_id  bigint not null references publications(id) on delete cascade,
  faculty_id      text   not null references faculty(faculty_id) on delete cascade,
  matched_author  text   not null,        -- the author string as it appeared
  author_position int,                    -- 1 = first author
  evidence        text,                   -- 'name and affiliation' or 'name only (...)'
  primary key (publication_id, faculty_id)
);

create index if not exists idx_publication_faculty_faculty
  on publication_faculty(faculty_id);

-- Use the view for reports. security_invoker makes it respect the caller's
-- permissions, so the locked-down tables cannot be read through it.
create or replace view home_institution_publications
with (security_invoker = true) as
select p.*,
       array_agg(f.full_name order by pf.author_position) as faculty_names,
       array_agg(f.faculty_id order by pf.author_position) as faculty_ids
from publications p
join publication_faculty pf on pf.publication_id = p.id
join faculty f on f.faculty_id = pf.faculty_id
group by p.id;

-- Lock the tables against the public API. The connector uses the database role,
-- which is not affected by these policies.
alter table publications          enable row level security;
alter table journal_details       enable row level security;
alter table conference_details    enable row level security;
alter table faculty               enable row level security;
alter table publication_faculty   enable row level security;
