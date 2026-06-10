from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
import bcrypt
from jose import jwt, JWTError
from datetime import datetime, timedelta
from sqlalchemy import select
from db import AsyncSessionLocal, engine
from models import Base, User, Room, Message
import os
from dotenv import load_dotenv

load_dotenv()
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise ValueError("SECRET_KEY не задан в .env")
ALGORITHM = "HS256"
active_collections = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        for room_name in ["general" , "random", "tech"]:
            result = await session.execute(select(Room).where(Room.name == room_name))
            if not result.scalar_one_or_none():
                session.add(Room(name=room_name))
        await session.commit()
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
class UserRegister(BaseModel):
    username: str
    password: str
class UserLogin(BaseModel):
    username:str
    password:str

@app.post("/register")
async def register(user_register: UserRegister):
   async with AsyncSessionLocal() as session:
       res = await session.execute(select(User).where(User.username == user_register.username))
       if res.scalar_one_or_none():
            raise HTTPException(status_code=400 , detail="Пользователь уже зарегистрирован")
       hashed_pssword = bcrypt.hashpw(user_register.password.encode(), bcrypt.gensalt())
       hashed_password = hashed_pssword.decode()
       new_user = User(username=user_register.username, password=hashed_password)
       session.add(new_user)
       await session.commit()
       return {"message" : "ok"}
@app.post("/login")
async def login(user_register: UserLogin):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == user_register.username))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=400 , detail="Пользователь не найден")
        if not bcrypt.checkpw(user_register.password.encode() , row["password"].encode()):
            raise HTTPException(status_code=400 , detail="Неверный пароль")
        payload = {"sub" : str(row.id) , "exp": datetime.utcnow() + timedelta(hours=24)}
        token = jwt.encode(payload, secret_key, algorithm=ALGORITHM)
        return {"token" : token}
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket:WebSocket, room_id:int):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return
    try:
        payload = jwt.decode(token , secret_key , algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except JWTError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if room_id not in active_collections:
        active_collections[room_id] = []
    active_collections[room_id].append(websocket)
    print(f"пользователь {user_id} подключился к комнате {room_id}")
    try:
        while True:
            text = await websocket.receive_text()
            async with AsyncSessionLocal() as session:
                msg = Message(user_id=user_id, room_id =room_id, text=text)
                session.add(msg)
                await session.commit()
                for i in active_collections[room_id]:
                       if i != websocket:
                           await i.send_text(text)

    except WebSocketDisconnect:
        active_collections[room_id].remove(websocket)
@app.get("/")
async def root():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Чат</title>
        <style>
            body { font-family: monospace; max-width: 800px; margin: 20px auto; background: #1e1e1e; color: #d4d4d4; }
            #messages { border: 1px solid #444; height: 400px; overflow-y: auto; background: #252526; }
            .message { margin: 5px; padding: 8px; border-radius: 5px; }
            .my { background: #2a6d4e; text-align: right; }
            .other { background: #3e3e42; }
            input, button { background: #3c3c3c; border: none; color: white; padding: 10px; }
        </style>
    </head>
    <body>
        <div id="auth">
            <input id="username" placeholder="Имя"><input id="password" type="password" placeholder="Пароль">
            <button id="reg">Регистрация</button><button id="login">Вход</button>
        </div>
        <div id="chat" style="display:none">
            <div id="messages"></div>
            <input id="message" placeholder="Сообщение"><button id="send">Отправить</button>
        </div>
        <script>
            let ws = null, token = null;
            async function apiCall(url, data) {
                const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
                if (!res.ok) throw new Error((await res.json()).detail);
                return res.json();
            }
            document.getElementById('reg').onclick = async () => {
                try { await apiCall('/register', { username: username.value, password: password.value }); alert('ok'); }
                catch(e) { alert(e.message); }
            };
            document.getElementById('login').onclick = async () => {
                try { const data = await apiCall('/login', { username: username.value, password: password.value }); token = data.token; auth.style.display='none'; chat.style.display='block'; connect(); }
                catch(e) { alert(e.message); }
            };
            function connect() {
                ws = new WebSocket(`ws://localhost:8000/ws/1?token=${token}`);
                ws.onmessage = e => { const d = document.createElement('div'); d.className='message other'; d.textContent=e.data; messages.appendChild(d); };
                ws.onopen = () => { loadHistory(); };
            }
            async function loadHistory() {
                const res = await fetch(`/history/1?token=${token}`);
                const msgs = await res.json();
                msgs.forEach(m => { const d = document.createElement('div'); d.className='message other'; d.textContent=m.username+': '+m.text; messages.appendChild(d); });
            }
            document.getElementById('send').onclick = () => {
                if(ws && message.value) { ws.send(message.value); const d = document.createElement('div'); d.className='message my'; d.textContent=message.value; messages.appendChild(d); message.value=''; }
            };
        </script>
    </body>
    </html>
    """)


















