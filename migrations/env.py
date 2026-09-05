from logging.config import fileConfig

from alembic import context
from alembic.util.exc import CommandError
from sqlalchemy import engine_from_config, pool

from app import db
import app.models  # noqa: F401 - registers all legacy tables in db.metadata


config = context.config

if config.config_file_name is not None and config.get_section("loggers"):
    fileConfig(config.config_file_name)

target_metadata = db.metadata


def resolve_database_url() -> str:
    """Resolve the migration target without ever defaulting to a scratch database.

    Precedence:

    1. an explicit programmatic ``sqlalchemy.url`` (used by the migration test
       helpers so they keep full control of their disposable target); then
    2. the running Flask application's configured ``SQLALCHEMY_DATABASE_URI``,
       which is what ``flask --app main:app db ...`` must operate on.

    ``alembic.ini`` deliberately ships no URL. When neither source resolves, this
    raises instead of silently migrating an in-memory database that is discarded
    when the command exits.
    """
    explicit_url = config.get_main_option("sqlalchemy.url", None)
    if explicit_url:
        return explicit_url

    try:
        from flask import current_app

        application_url = current_app.config["SQLALCHEMY_DATABASE_URI"]
    except (ImportError, KeyError, RuntimeError) as exc:
        raise CommandError(
            "No migration target is configured. Run this through the Flask "
            "application (for example 'flask --app main:app db upgrade') so the "
            "configured SQLALCHEMY_DATABASE_URI is used, or set sqlalchemy.url "
            "programmatically."
        ) from exc

    if not application_url:
        raise CommandError(
            "The Flask application has no SQLALCHEMY_DATABASE_URI configured."
        )

    # Returned verbatim. Both callers consume this value directly rather than
    # writing it back through Alembic's configparser, so no '%' escaping is
    # applied -- escaping here would corrupt a password or query string that
    # legitimately contains '%'. (The helper path escapes on set_main_option and
    # get_main_option unescapes it again, so it also arrives here verbatim.)
    return str(application_url)


def run_migrations_offline() -> None:
    context.configure(
        url=resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = resolve_database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
