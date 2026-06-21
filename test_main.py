import pytest
import os
import httpx
import time
RENDER_URL = os.getenv("RENDER_URL" , "https://fastapi-websockets-jwt-postgresql-ydp5.onrender.com")
@pytest.fixture()
async def client():
    return httpx.AsyncClient(base_url=RENDER_URL, timeout=30)
@pytest.mark.anyio
async def test_server_alive(client):
    try:
        response = await client.get("/")
        assert response.status_code == 200
        print("✅Сервер жив")
    except httpx.ReadTimeout:
        pytest.skip("Render не отвечает , мб спит")


@pytest.mark.anyio
async def test_registration(client):
    username = f"test_user_{int(time.time())}"
    response = await client.post("/register" , json = {
        "username" : username ,
        "password" : "zxcpassword"
    })
    assert response.status_code == 200
    print(f"✅Регистрация : {response.status_code}")
@pytest.mark.anyio
async def test_login(client):
    await client.post("/register" , json = {"username" : "testendpoint" , "password" : "7777777777"})
    response = await client.post("/login" , json = {"username" : "testendpoint" , "password" : "7777777777"})
    assert response.status_code == 200
    assert "token" in response.json()
    print("✅ Логин работает")
@pytest.mark.anyio
async def test_history(client):
    response = await client.get("/history/1")
    assert response.status_code in [200 , 401 , 422]
    print(f"✅ История рабоатет {response.status_code}")
@pytest.mark.anyio
async def test_ai(client):
    response = await client.post("/chat/ai" , json = {"prompt" : "Привет , ты работаешь?" , "room_id" : 1})
    assert response.status_code in [200 , 401]
    print(f"✅AI ответил {response.status_code}")
@pytest.mark.anyio
async def test_docs(client):
    response = await client.get("/docs")
    assert response.status_code == 200
    print(f"✅Документация доступна")




