Writes rows from Keboola Storage tables into Helios iNuvio via the iNuvio Server System API.

### Connection

Set the iNuvio Server base URL, the SQL user credentials (username and password), and the target Helios database (the `dbName` used on every API call). Use **Test Connection** to verify the credentials before saving.

### Write target (per row)

- **Browse** — the Helios browse (přehled) to write into. The list is loaded from the server and limited to browses your user can create or update.
- **Write Mode** — `Create` issues a `POST` for each input row. It is **not idempotent** and the writer keeps no checkpoint, so re-running (or resuming after a partial failure) inserts the rows again and creates duplicates in Helios; use `Update` where idempotent writes are required. `Update` issues a `PUT` and requires a **Key Field** holding the Helios record id.
- **Field Mapping** — maps input table columns to Helios fields. Use **Re-load Mapping** to fetch the writable fields for the selected browse and pre-fill the mapping; existing assignments are preserved.
- **Continue on Error** — when enabled, rows the API rejects are written to `failed_records.csv` (with the error message) instead of failing the whole job.
