# Investment Operating System M1 Database Gate

## Purpose

This runbook is a release-blocking evidence gate for every active database
environment. It is a procedure and record template, not evidence that any
environment has passed review.

No active database may be generated from, stamped, or upgraded unless its
signed gate is complete and records `BASELINE_EQUIVALENT=true`.

Missing, incomplete, stale, or unsigned evidence blocks all of the following:

- generating a migration from an active database;
- stamping an active database, including revision `20260904_01`;
- upgrading an active database; and
- Plan 5 Task 1 deployment activity.

Repository-safe generation, review, and testing against disposable temporary
databases remain allowed. Those activities must use explicitly constructed
temporary database URLs and must not contact an active database.

## Evidence handling rules

Create one separately reviewed record for each active environment and store the
signed evidence in the controlled deployment record. Never store any of the
following in this document, an inventory, or its supporting evidence:

- a database URL or connection string;
- a password, secret, token, or credential;
- application row data; or
- unredacted command output containing any of the above.

Use approved secret-management channels to supply credentials at execution
time. Evidence may contain sanitized commands only; replace sensitive values
with environment-variable names or redaction markers.

## Required evidence for each active environment

The record is incomplete until every field below has a value and the reviewer
has explicitly approved baseline equivalence.

### Environment and connection metadata

- Environment identifier: a stable, non-secret name for the environment.
- SQL dialect: the dialect reported by the schema inventory.
- SQL driver: the driver reported by the schema inventory.
- Evidence record identifier or controlled-record reference.

### Backup and restoration readiness

- Secured backup confirmation: backup identifier, completion status, storage
  control/reference, and confirmation that access is restricted.
- Approved backup procedure/reference.
- Exact backup command used, sanitized to remove URLs and credentials.
- Restore verification: isolated restore target identifier, verification result,
  and verification timestamp.
- Exact restore-verification command used, sanitized to remove URLs and
  credentials.

The active database platform's approved backup procedure must be selected only
after the inventory identifies the dialect and hosting platform. Do not infer a
backup command before then.

### Current database state

- Current Alembic state: whether `alembic_version` exists and every current
  `version_num` row.
- Redacted schema inventory path and immutable artifact/reference identifier.
- Confirmation that the inventory contains schema metadata only and no row
  data, database URL, or credentials.

Run the read-only inventory using the environment-variable indirection below:

```powershell
python scripts/inspect_database_schema.py --database-url-env DATABASE_URL --output "$env:IOS_M1_AUDIT_DIR/schema-inventory.json"
```

The output must be reviewed and redacted before it enters the controlled
deployment record.

The inventory reports `check_constraints` for every table and a top-level
`native_enums` section. `native_enums` is `{"supported": false, "enums": []}` on
dialects with no native enum catalogue, which states the absence explicitly
rather than implying no enum types exist.

Enum representation is dialect-dependent and must be reviewed accordingly. The
legacy models declare `sa.Enum` without `native_enum=False`, and SQLAlchemy
defaults `create_constraint` to false. On SQLite the enum columns are therefore
plain `VARCHAR` with no CHECK constraint, so the allowed values appear nowhere
in the reflected schema. On a dialect with a native enum catalogue the same
models create real enum types, which `native_enums` reports. Enum drift must
therefore be reviewed against the actual deployment dialect; a SQLite inventory
cannot evidence it.

### Model-to-schema comparison and drift disposition

- Model-to-schema comparison result against the six-table legacy baseline:
  `user`, `ticker`, `trade`, `tag`, `telegram_verification`, and `trade_tags`.
- A complete list of every drift item, including missing or extra tables,
  columns, types, nullability, defaults, indexes, keys, and constraints.
- An explicit approved disposition for every drift item. No drift may be left
  unexplained, deferred without approval, or omitted from the record.
- Confirmation that stamping would introduce only `alembic_version` and would
  not create or modify a legacy, M1, research, document, or entitlement table.

If any drift means the existing schema is not equivalent to revision
`20260904_01`, record `BASELINE_EQUIVALENT=false` and stop. Resolve and review
the drift through an approved process before repeating this gate.

### Human review and approval

- Reviewer identity and reviewer/reference identifier.
- Review timestamp including timezone.
- Reviewer confirmation that backup and restore evidence is valid.
- Reviewer confirmation that the redacted inventory and model comparison cover
  the correct active environment.
- Reviewer confirmation that every drift has an approved disposition.
- Reviewer signature or controlled approval reference.
- The exact approval statement `BASELINE_EQUIVALENT=true`.

The reviewer may record `BASELINE_EQUIVALENT=true` only when the existing
schema is proven equivalent to the legacy baseline and stamping will add only
Alembic's version marker. A missing or different value is not approval.

## Gate decision

Before any generate-from, stamp, or upgrade operation, the deployment operator
and reviewer must verify all of the following:

- the evidence belongs to the exact active environment being operated on;
- the backup is secured and its restore has been verified;
- the inventory and Alembic state are current;
- model-to-schema comparison is complete;
- every drift has an explicit approved disposition;
- reviewer identity, reference, timestamp, and signature are present; and
- the signed record contains `BASELINE_EQUIVALENT=true`.

If any check fails, stop. Do not generate from, stamp, or upgrade the active
database.

## Destructive downgrade warning

**Never run `downgrade` of the legacy baseline `20260904_01` against a shared,
staging, or production database.**

Its `downgrade()` drops the six legacy tables — `trade_tags`, `trade`,
`telegram_verification`, `tag`, `user`, `ticker` — destroying all user, ticker,
trade, tag, and Telegram data. It is not a rollback mechanism.

On a verified existing deployment the baseline is applied by `stamp`, which
writes only the `alembic_version` marker and creates no table. Downgrading it
would therefore drop tables that this revision never created on that database.

- Baseline `downgrade()` is permitted on disposable local and CI databases only.
- To roll back Milestone 1, downgrade the additive revision `20260904_02`, which
  drops only the new Investment Operating System tables and leaves the legacy
  schema intact.
- To restore the legacy schema or its data, use the verified backup this gate
  requires. The backup must be retained until rollback/restore acceptance is
  signed.

## Migration target resolution

Alembic resolves its target database from the running Flask application's
configured `SQLALCHEMY_DATABASE_URI`; `migrations/alembic.ini` intentionally
contains no `sqlalchemy.url`. Always run stamp, upgrade, and `db current`
through the application, for example `flask --app main:app db current`, so the
command reports on the intended database. If no target resolves, the command
fails with an explicit error rather than silently operating on a scratch
in-memory database. Never add a deployment URL or credentials to `alembic.ini`
or any other tracked file; supply `DATABASE_URL` through the approved
secret-management channel at execution time.
