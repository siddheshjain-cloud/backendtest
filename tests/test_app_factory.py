def test_app_fixture_uses_isolated_configuration(app):
    assert app.config["TESTING"] is True
    assert app.config["ELASTICSEARCH_URL"] is None
    assert "test-" in app.config["SQLALCHEMY_DATABASE_URI"]
