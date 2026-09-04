import uuid

import pytest
from flask_jwt_extended import create_access_token

from app import create_app, db
from app.models.user import User
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
    return user_factory(email="premium@example.com")
