Helios iNuvio Writer
====================

The Helios iNuvio Writer is a generic Keboola writer that pushes rows from Keboola
Storage tables into [Helios iNuvio](https://www.helios.eu/) (Asseco Solutions ERP) via
its iNuvio Server / eServer REST API. Each configuration row maps one input table to one
writable Helios browse: you pick the browse from a dynamic dropdown, map your input
columns onto the browse's writable fields, and the writer creates or updates one Helios
record per input row. There is no fixed, hard-coded entity list — any browse the server
reports as writable can be targeted.

**Table of Contents:**

[TOC]

Prerequisites
=============

- A Helios iNuvio installation with the **iNuvio Server / eServer** module enabled and
  reachable over HTTP(S).
- A Helios user with REST **create/update** rights on the browses you intend to write to.
- The connection details for that installation:
  - **Server URL** of the iNuvio Server / eServer (default eServer HTTP port is `8080`).
  - **Username** and **Password** of the Helios user.
  - **Database** name (e.g. `HEE_Helios_001`).

Standing up the iNuvio Server / eServer module and provisioning the user is a one-time
admin task on the Helios installation; the component does not automate it.

Configuration
=============

Configuration is split into a connection (config root level) and one or more write
targets (config row level). One config row maps exactly one input table to one browse.

Connection (config root level)
------------------------------

| Field | Description |
|-------|-------------|
| Server URL | iNuvio Server / eServer base URL, e.g. `http://localhost:8080`. |
| Username | Helios user with REST write rights. |
| Password | Password for that user (stored encrypted). |
| Database | Helios database name, e.g. `HEE_Helios_001`. |
| Test Connection | Button that validates the server URL, credentials, and database by obtaining a token. |

Write target (config row level)
-------------------------------

| Field | Description |
|-------|-------------|
| Browse | The Helios browse to write to. Populated by the **Re-load browses** button from the browses the server reports as writable. Each option reads as `Browse Name (id)`; entering a bare id also works. |
| Write Mode | `Create` (insert new records) or `Update` (modify existing records by key). |
| Key Field | The record-identifier column used to match records in **Update** mode. Required when Write Mode is `Update`. |
| Field Mapping | Maps input columns to the browse's writable Helios fields. Use the **Re-load mapping** button to populate it: the writer reads the input table's columns, fetches the browse's writable fields, and fuzzy-matches them. Any mapping you have already set is preserved. Unmapped columns are skipped. |
| Continue on Error | When enabled, rejected rows are written to `failed_records.csv` instead of failing the job. Default: off. |
| Debug | Enables verbose debug logging. Default: off. |

Output
======

On success the writer produces **no output tables** — it only writes records to Helios.

When **Continue on Error** is enabled and one or more rows are rejected, the writer
produces a `failed_records.csv` table listing the failed input rows plus an `error`
column describing why each row failed. This table is always written when failures occur,
so the failure log survives even a non-zero job result.

Note on **Create** mode: each input row is inserted with a separate, non-idempotent `POST`,
and the writer keeps no record of what it already wrote. Re-running a configuration (or
resuming after a partial failure) therefore **inserts the rows again, creating duplicates**
in Helios. Use **Update** mode with a Key Field where idempotent writes are required.

Development
===========

To customize the local data folder path, replace the `CUSTOM_FOLDER` placeholder with
your desired path in the `docker-compose.yml` file:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    volumes:
      - ./:/code
      - ./CUSTOM_FOLDER:/data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone this repository, initialize the workspace, and run the component using the
following commands:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
git clone https://github.com/keboola/component-wr-helios-inuvio component-helios-inuvio-writer
cd component-helios-inuvio-writer
docker-compose build
docker-compose run --rm dev
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the test suite (lint + unit + functional tests) in Docker with:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
docker-compose run --rm test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Or run the tests directly with [uv](https://docs.astral.sh/uv/):

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
uv run pytest
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The functional tests use VCR: they replay recorded HTTP cassettes (under
`tests/functional/*/source/data/cassettes/`), so no live Helios installation is needed
to run them in CI. Connection secrets are kept out of the repository — local development
reads them from a gitignored `secrets.json`.

Integration
===========

For details about deployment and integration with Keboola, refer to the
[deployment section of the developer
documentation](https://developers.keboola.com/extend/component/deployment/).
