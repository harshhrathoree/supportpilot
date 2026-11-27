from fastapi.testclient import TestClient

from supportpilot.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page_serves_the_review_screen():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "SupportPilot" in response.text


def test_review_screen_never_writes_server_data_with_innerhtml():
    """Ticket text is written by customers. innerHTML would let one run a script in a reviewer's browser."""
    page = client.get("/").text
    assert "innerHTML" not in page
