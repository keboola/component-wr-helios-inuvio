The Helios iNuvio Writer pushes data from Keboola Storage tables into Helios iNuvio
(Asseco Solutions ERP) via its iNuvio Server / eServer REST API.

Each configuration row maps one input table to one Helios browse. You select the
browse from a dynamic dropdown, then map input columns to the browse's writable
fields. The writer supports **create** (insert) and **update** (modify by key) modes.
When "Continue on Error" is enabled, rejected rows are written to `failed_records.csv`
instead of failing the job.

Connect by entering the server URL, username, password, and database; use **Test
Connection** to validate credentials.
