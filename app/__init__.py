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


def create_app(config_class=Config):
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

    # Register blueprints
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

    # Error handlers
    from app.utils.error_handlers import register_error_handlers
    register_error_handlers(app)

    # Elasticsearch
    app.elastic_search = Elasticsearch([app.config['ELASTICSEARCH_URL']]) \
        if app.config['ELASTICSEARCH_URL'] else None

    return app