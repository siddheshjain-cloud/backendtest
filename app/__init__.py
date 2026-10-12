from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from elasticsearch import Elasticsearch
from config import Config
from sqlalchemy import event


db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()

_DEFAULT_SQLITE_URI = "sqlite:///trading_app.db"


def _require_explicit_database_opt_in(
    config_class, *, allow_default_database: bool
) -> None:
    """Fail closed against the exact incident this guards against: an
    ad-hoc script or debugging session calling ``create_app()`` with no
    arguments, silently landing on ``config.py``'s bare fallback URI --
    which resolves to the real ``instance/trading_app.db`` live
    database, since no ``.env``/``DATABASE_URL`` is configured in this
    environment at all.

    Deliberately a pure, dependency-free check (no Flask/db app needed)
    so it can run before any app/engine is constructed, and so it is
    directly unit-testable.

    Never fires for `TestingConfig` (the `tests/conftest.py` ``app``
    fixture overrides ``SQLALCHEMY_DATABASE_URI`` to a per-test
    ``tmp_path`` file *before* calling ``create_app()``, so by the time
    this runs the URI already differs from the bare default) and never
    fires when a real deployment has set ``DATABASE_URL`` to anything
    else, including another sqlite path -- only the exact, untouched
    default string is treated as ambiguous.

    The three legitimate entrypoints that intend to use that real
    database (``main.py``, ``console.py``, ``live/websocket.py``) pass
    ``allow_default_database=True`` explicitly, in their own source
    code, as a one-line, auditable acknowledgement -- not an
    environment variable, which ``load_dotenv()`` would apply equally
    to an ad-hoc script and so could not actually distinguish the two.
    """

    if allow_default_database:
        return
    if config_class.SQLALCHEMY_DATABASE_URI != _DEFAULT_SQLITE_URI:
        return
    raise RuntimeError(
        "create_app() was called with no DATABASE_URL configured and no "
        "explicit opt-in, which would silently connect to the real "
        f"{_DEFAULT_SQLITE_URI!r} live database. If this is a test, use "
        "the tests/conftest.py `app` fixture (it overrides the URI "
        "automatically). If this is a one-off script or debugging "
        "session, do not connect to the live database directly -- build "
        "an isolated copy instead (see any Phase 3 migration test for "
        "the sqlite3.Connection.backup() pattern already used in this "
        "codebase). If you really are a legitimate entrypoint that must "
        "use the live database, pass create_app(allow_default_database="
        "True) explicitly, the same way main.py/console.py/"
        "live/websocket.py already do."
    )


def create_app(
    config_class=Config, *, register_research=False, allow_default_database=False
):
    """Build the Flask app.

    ``register_research=False`` (the default) builds the legacy-only app:
    auth/users/trades/tickers/tags/telegram, with no research or document
    blueprint registered, so no ``app.services.research``,
    ``app.services.document``, or ``app.policies`` module is imported. This
    is the default specifically so that ``live/websocket.py``'s frozen,
    zero-argument ``create_app()`` call -- used only to give ``TickerManager``
    a database-bound app context, never to serve research/document HTTP
    traffic -- never pulls research/document code into the live ticker
    process. Callers that serve the full API (the WSGI entrypoint, the
    admin console, and tests that exercise research/document routes) must
    pass ``register_research=True`` explicitly.

    ``allow_default_database=False`` (the default) fails closed against
    ad-hoc scripts/debugging sessions silently connecting to the real
    live database -- see ``_require_explicit_database_opt_in``'s own
    docstring for the full incident this guards against. The three
    legitimate entrypoints that must use that database
    (``main.py``/``console.py``/``live/websocket.py``) pass
    ``allow_default_database=True`` explicitly.
    """

    _require_explicit_database_opt_in(
        config_class, allow_default_database=allow_default_database
    )

    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    CORS(app)
    app.url_map.strict_slashes = False

    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:

        with app.app_context():
            @event.listens_for(db.engine, "connect")
            def enable_wal(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.close()

    # Register legacy blueprints -- always, for every caller.
    from app.routes.auth import auth_bp
    from app.routes.users import users_bp
    from app.routes.trades import trades_bp
    from app.routes.tickers import tickers_bp
    from app.routes.tags import tags_bp
    from app.routes.telegram import telegram_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(users_bp, url_prefix='/api/users')
    app.register_blueprint(trades_bp, url_prefix='/api/trades')
    app.register_blueprint(tickers_bp, url_prefix='/api/tickers')
    app.register_blueprint(tags_bp, url_prefix='/api/tags')
    app.register_blueprint(telegram_bp, url_prefix='/api/telegram')

    # Register research/document blueprints only when explicitly requested.
    if register_research:
        from app.routes.research import research_bp
        from app.routes.documents import documents_bp
        from app.routes.admin_research import admin_research_bp, admin_entitlements_bp
        from app.routes.admin_documents import admin_documents_bp
        from app.routes.admin_alerts import admin_alerts_bp

        app.register_blueprint(research_bp, url_prefix='/api/research')
        app.register_blueprint(documents_bp, url_prefix='/api/research')
        app.register_blueprint(admin_research_bp, url_prefix='/api/admin/research')
        app.register_blueprint(admin_documents_bp, url_prefix='/api/admin/research')
        app.register_blueprint(admin_entitlements_bp, url_prefix='/api/admin/users')
        app.register_blueprint(admin_alerts_bp, url_prefix='/api/admin/alerts')

    # Error handlers
    from app.utils.error_handlers import register_error_handlers
    register_error_handlers(app)

    # Elasticsearch
    app.elastic_search = Elasticsearch([app.config['ELASTICSEARCH_URL']]) \
        if app.config['ELASTICSEARCH_URL'] else None

    return app
