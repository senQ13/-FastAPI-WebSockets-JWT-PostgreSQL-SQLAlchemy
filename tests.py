from fastapi.testclient import TestClient
from backendmain import app
import pytest
@pytest.fixture(scope="session")
def client():
    with TestClient(app) as client:
        yield client
def test_register(client):
    response = client.post("/register", json = {
        "username" : "testus27",
        "password" : "123"
    })
    assert response.status_code == 200
    assert response.json() == {"message" : "ok"}
def test_login(client):
    response = client.post("/login", json = {
        "username" : "testus22",
        "password" : "123"
    })
    assert response.status_code == 200
def test_register2(client):
    response = client.post("/register" , json = {
        "username" : "testus22",
        "password" : "123"
    })
    assert response.status_code == 400
def test_wrong_password(client):
    response = client.post("/register" , json = {
        "username" : "testus22",
        "password" : ""
    })
    assert response.status_code == 400

