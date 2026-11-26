# Reference data

`scimagojr_2025.csv`: SCImago Journal Rank export, used for field 14 (Quartile).
Downloaded from https://www.scimagojr.com/journalrank.php ("Download data").
The newest `scimagojr*.csv` in this folder is used automatically.

To update it each year, download the new file, save it here (e.g. `scimagojr_2026.csv`),
and delete the old one.

`faculty.csv`: home-institution teachers, one row each, used for fields 25 and 26.

| Column | Meaning |
|--------|---------|
| `faculty_id` | The teacher's unique ID, e.g. `SNUC-F0007`. Give a new teacher the next free number. Never change or reuse an ID. |
| `full_name` | Name as it should be displayed |
| `given_names` | Given name, or initials separated by spaces (`K S`) |
| `family_name` | Surname, one word (a single letter if only the initial is known) |
| `name_variants` | Optional. Other spellings the matching rules can't reach, separated by `;` |

The file is checked when it is read. A mistake (duplicate ID, missing surname...) is shown in
`/api/health` under `faculty.error`, and the previous list stays in use.
