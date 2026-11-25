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

-- Home-institution faculty. Each professor gets a UID that is generated once and never changes.
create table if not exists faculty (
  faculty_uid uuid primary key default gen_random_uuid(),
  full_name   text not null unique,      -- display name, e.g. 'K.S. Gayathri'
  given_names text not null,             -- e.g. 'Sreehari', or 'K S' for initials
  family_name text not null,             -- surname; a single letter if only the initial is known
  created_at  timestamptz not null default now()
);

-- Spellings the matching rules cannot reach. The full name is always matched as well.
create table if not exists faculty_name_variants (
  faculty_uid  uuid not null references faculty(faculty_uid) on delete cascade,
  name_variant text not null,
  primary key (faculty_uid, name_variant)
);

-- Which home-institution professors wrote which publication.
create table if not exists publication_faculty (
  publication_id  bigint not null references publications(id) on delete cascade,
  faculty_uid     uuid   not null references faculty(faculty_uid) on delete cascade,
  matched_author  text   not null,       -- the author string as it appeared
  author_position int,                   -- 1 = first author
  primary key (publication_id, faculty_uid)
);

create index if not exists idx_publication_faculty_faculty
  on publication_faculty(faculty_uid);

-- Names are hard-coded here. Re-running is safe.
insert into faculty (full_name, given_names, family_name) values
  ('Sreehari Veeramachaneni', 'Sreehari', 'Veeramachaneni'),
  ('Suganya E',               'Suganya',   'E'),            -- CONFIRM the surname
  ('K.S. Gayathri',           'K S',       'Gayathri'),
  ('N. Sripriya',             'N',         'Sripriya'),
  ('R. Swathika',             'R',         'Swathika'),
  ('Durgadevi Velusamy',      'Durgadevi', 'Velusamy'),
  ('Saravanan Alagarsamy',    'Saravanan', 'Alagarsamy')
on conflict (full_name) do update
    set given_names = excluded.given_names,
        family_name = excluded.family_name;

-- Add a variant only for a spelling the rules cannot reach, for example:
-- insert into faculty_name_variants select faculty_uid, 'Suganya Eswaran'
--   from faculty where full_name = 'Suganya E';

-- Use the view for reports. security_invoker makes it respect the caller's
-- permissions, so the locked-down tables cannot be read through it.
create or replace view home_institution_publications
with (security_invoker = true) as
select p.*,
       array_agg(f.full_name order by pf.author_position) as faculty_names
from publications p
join publication_faculty pf on pf.publication_id = p.id
join faculty f on f.faculty_uid = pf.faculty_uid
group by p.id;

-- Lock the tables against the public API. The connector uses the database role,
-- which is not affected by these policies.
alter table publications          enable row level security;
alter table journal_details       enable row level security;
alter table conference_details    enable row level security;
alter table faculty               enable row level security;
alter table faculty_name_variants enable row level security;
alter table publication_faculty   enable row level security;