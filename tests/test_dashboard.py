import pytest
from flask.testing import FlaskClient
from location_tracker.dashboard import app

@pytest.fixture
def client():
    app.config["TESTING"] = True
    client = app.test_client()
    yield client

def test_dashboard_api_endpoints(client):
    response = client.get("/api/locations")
    assert response.status_code == 200
    response = client.get("/api/stats")
    assert response.status_code == 200
    response = client.get("/api/stops")
    assert response.status_code == 200