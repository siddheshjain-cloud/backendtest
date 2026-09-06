import uuid
from types import SimpleNamespace

import pytest
from flask_jwt_extended import create_access_token

from app import create_app, db
from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.research_types import EntitlementStatus, ResearchTier
from app.models.ticker import Ticker
from app.models.trade import Trade
from app.models.user import User
from app.models.utils import TradeSide, TradeStatus, TradeTimeframe, TradeType
from config import Config


class TestingConfig(Config):
    TESTING = True
    JWT_SECRET_KEY = "test-jwt-secret"
    ELASTICSEARCH_URL = None


@pytest.fixture
def app(tmp_path):
    TestingConfig.SQLALCHEMY_DATABASE_URI = (
        f"sqlite:///{(tmp_path / f'test-{uuid.uuid4()}.db').as_posix()}"
    )
    application = create_app(TestingConfig)

    with application.app_context():
        import app.models

        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user_factory(app):
    def create_user(*, email: str, is_admin: bool = False) -> User:
        user = User(name=email.split("@")[0], email=email, is_admin=is_admin)
        user.set_password("test-password")
        db.session.add(user)
        db.session.commit()
        return user

    return create_user


@pytest.fixture
def ticker_factory(app):
    def create_ticker(
        *,
        symbol: str = "IKIO",
        instrument_token: int = 1,
        exchange: str = "NSE",
        name: str = "IKIO Technologies Limited",
        last_price: float = 100.0,
    ) -> Ticker:
        ticker = Ticker(
            symbol=symbol,
            instrument_token=instrument_token,
            exchange=exchange,
            name=name,
            last_price=last_price,
        )
        db.session.add(ticker)
        db.session.commit()
        return ticker

    return create_ticker


@pytest.fixture
def trade_factory(app):
    def create_trade(
        *,
        user: User,
        ticker: Ticker,
        side: str = TradeSide.BUY,
        trade_type: str = TradeType.CROSSING_ABOVE,
        status: str = TradeStatus.ACTIVE,
        entry: float = 101.0,
        stoploss: float | None = 99.0,
        target: float | None = 105.0,
        notes: str = "",
        timeframe: str = TradeTimeframe.DAY,
    ) -> Trade:
        trade = Trade(
            symbol=ticker.symbol,
            side=side,
            type=trade_type,
            status=status,
            entry=entry,
            stoploss=stoploss,
            target=target,
            notes=notes,
            timeframe=timeframe,
            user_id=user.id,
            ticker_id=ticker.id,
        )
        db.session.add(trade)
        db.session.commit()
        return trade

    return create_trade


@pytest.fixture
def candle_factory():
    def create_candle(*, high: float = 100.0, low: float = 100.0):
        return SimpleNamespace(high=high, low=low)

    return create_candle


@pytest.fixture
def auth_headers(app):
    def make_headers(user: User) -> dict[str, str]:
        with app.app_context():
            return {"Authorization": f"Bearer {create_access_token(identity=user.id)}"}

    return make_headers


@pytest.fixture
def admin_user(user_factory):
    return user_factory(email="admin@example.com", is_admin=True)


@pytest.fixture
def free_user(user_factory):
    return user_factory(email="free@example.com")


@pytest.fixture
def premium_user(user_factory):
    user = user_factory(email="premium@example.com")
    db.session.add(
        UserEntitlement(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
            tier=ResearchTier.PREMIUM,
            status=EntitlementStatus.ACTIVE,
        )
    )
    db.session.commit()
    return user
