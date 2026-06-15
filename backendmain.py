from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
import bcrypt
import httpx
from jose import jwt, JWTError
from fastapi import FastAPI, Request
from datetime import datetime, timedelta
from sqlalchemy import select
from db import AsyncSessionLocal, engine
from models import Base, User, Room, Message
import os
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from fastapi import UploadFile , File
import uuid
import shutil
os.makedirs("uploads", exist_ok=True)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
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
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
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
        if not bcrypt.checkpw(user_register.password.encode() , row.password.encode()):
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
@app.post("/uploadaudio")
async def uploadaudio(upload: UploadFile = File(...)):
    if upload.content_type not in ["audio/webm" , "audio/ogg"]:
        raise HTTPException(status_code=400, detail="Не верный формат файла")
    ext = upload.filename.split(".")[-1] if "." in upload.filename else "webm"
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = f"uploads/{filename}"
    with open(filepath, "wb") as f:
        shutil.copyfileobj(upload.file, f)

    return{"url" : f"/uploads/{filename}"}
@app.get("/history/{room_id}")
async def get_history(room_id:int , token : str):
    if not token:
        raise HTTPException(status_code=401 , detail = "Ошибка токена")
    try:
        jwt.decode(token, secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Ошибка токена")
    async with AsyncSessionLocal() as session:
        stmt = (select(User.username , Message.text , Message.created_at)
                .join(User, User.id == Message.user_id)
                .where(Message.room_id == room_id)
                .order_by(Message.created_at)
                .limit(50))
        res = await session.execute(stmt)
        row = res.all()
        return [{"username": r[0], "text": r[1], "created_at": r[2]} for r in row[::-1]]
@app.post("/webhook/test")
async def test_webhook(request: Request):
    data = await request.json()
    message = data.get("message" , "Уведомление от внешнего сервиса")
    room_id = data.get("room_id" , 1)
    if room_id in active_collections:
        for ws in active_collections[room_id]:
            await ws.send_text(f"Вебхук: {message}")
    return {"message" : "ок"}
@app.post("/webhook/telegram")
async def webhook_telegram(request: Request):
    data = await request.json()
    message = data.get("message" , {})
    message_text = message.get("text")
    if 1 in active_collections:
        for ws in active_collections[1]:
            await ws.send_text(f"Вебхук от тг : {message_text}")








@app.get("/")
async def root():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Чат с голосом</title>
        <style>
            body { font-family: monospace; max-width: 800px; margin: 20px auto; background: #1e1e1e; color: #d4d4d4; }
            #messages { border: 1px solid #444; height: 400px; overflow-y: auto; background: #252526; margin-top: 10px; }
            .message { margin: 5px; padding: 8px; border-radius: 5px; }
            .my { background: #2a6d4e; text-align: right; }
            .other { background: #3e3e42; }
            input, button, select { background: #3c3c3c; border: none; color: white; padding: 10px; margin: 5px; }
            button:hover { background: #555; cursor: pointer; }
            audio { max-width: 200px; display: block; }
            .room-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <div id="auth">
            <input id="username" placeholder="Имя">
            <input id="password" type="password" placeholder="Пароль">
            <button id="reg">Регистрация</button>
            <button id="login">Вход</button>
        </div>
        <div id="chat" style="display:none">
            <div class="room-bar">
                <label>Комната:</label>
                <select id="roomSelect">
                    <option value="1">general</option>
                    <option value="2">random</option>
                    <option value="3">tech</option>
                </select>
                <button id="joinRoom">Перейти</button>
                <button id="logoutBtn">Выйти</button>
            </div>
            <div id="messages"></div>
            <div style="display: flex; gap: 10px; margin-top: 10px;">
                <input id="message" placeholder="Текст" style="flex: 1;">
                <button id="send">Отправить</button>
                <button id="recordBtn" style="background: #d32f2f;">🎤 Запись</button>
                <button id="stopBtn" style="background: #555; display: none;">⏹ Стоп</button>
            </div>
        </div>
        <script>
            let ws = null, token = null;
            let mediaRecorder = null;
            let audioChunks = [];
            let isRecording = false;
            let currentRoomId = "1";

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
            document.getElementById('joinRoom').onclick = () => {
                if (ws) ws.close();
                connect();
            };
            document.getElementById('logoutBtn').onclick = () => {
                if (ws) ws.close();
                token = null;
                auth.style.display='block';
                chat.style.display='none';
                messages.innerHTML = '';
            };
            function connect() {
                currentRoomId = document.getElementById('roomSelect').value;
                ws = new WebSocket(`ws://localhost:8000/ws/${currentRoomId}?token=${token}`);
                ws.onmessage = e => { 
                    try {
                        const data = JSON.parse(e.data);
                        if (data.type === 'voice') {
                            const d = document.createElement('div'); 
                            d.className = 'message other'; 
                            const audio = document.createElement('audio');
                            audio.controls = true;
                            audio.src = data.url;
                            d.appendChild(audio);
                            messages.appendChild(d); 
                        } else {
                            const d = document.createElement('div'); 
                            d.className = 'message other'; 
                            d.textContent = data.text || e.data; 
                            messages.appendChild(d); 
                        }
                    } catch {
                        const d = document.createElement('div'); 
                        d.className = 'message other'; 
                        d.textContent = e.data; 
                        messages.appendChild(d); 
                    }
                    messages.scrollTop = messages.scrollHeight;
                };
                ws.onopen = () => {
                    console.log('WebSocket connected to room', currentRoomId);
                    loadHistory();
                };
            }

            async function loadHistory() {
                try {
                    const res = await fetch(`/history/${currentRoomId}?token=${token}`);
                    const msgs = await res.json();
                    messages.innerHTML = '';
                    for (let msg of msgs) {
                        const d = document.createElement('div');
                        d.className = 'message other';
                        if (msg.text && msg.text.startsWith('{"type":"voice"')) {
                            try {
                                const voiceData = JSON.parse(msg.text);
                                if (voiceData.type === 'voice') {
                                    const audio = document.createElement('audio');
                                    audio.controls = true;
                                    audio.src = voiceData.url;
                                    d.appendChild(audio);
                                } else {
                                    d.textContent = `${msg.username}: ${msg.text}`;
                                }
                            } catch(e) {
                                d.textContent = `${msg.username}: ${msg.text}`;
                            }
                        } else {
                            d.textContent = `${msg.username}: ${msg.text}`;
                        }
                        messages.appendChild(d);
                    }
                    messages.scrollTop = messages.scrollHeight;
                } catch(e) { 
                    console.error('Ошибка загрузки истории:', e); 
                }
            }

            async function uploadAudio(blob) {
                const formData = new FormData();
                formData.append('file', blob, 'voice.webm');
                const res = await fetch('/uploadaudio', { method: 'POST', body: formData });
                if (!res.ok) throw new Error('Ошибка загрузки');
                const data = await res.json();
                ws.send(JSON.stringify({ type: 'voice', url: data.url }));
            }

            document.getElementById('recordBtn').onclick = async () => {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
                mediaRecorder.onstop = () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    uploadAudio(audioBlob);
                    stream.getTracks().forEach(track => track.stop());
                };
                mediaRecorder.start();
                isRecording = true;
                recordBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            };
            document.getElementById('stopBtn').onclick = () => {
                if (mediaRecorder && isRecording) {
                    mediaRecorder.stop();
                    isRecording = false;
                    recordBtn.style.display = 'inline-block';
                    stopBtn.style.display = 'none';
                }
            };
            document.getElementById('send').onclick = () => {
                if(ws && message.value) { 
                    ws.send(JSON.stringify({ type: 'text', text: message.value })); 
                    const d = document.createElement('div'); 
                    d.className = 'message my'; 
                    d.textContent = message.value; 
                    messages.appendChild(d); 
                    message.value = ''; 
                    messages.scrollTop = messages.scrollHeight;
                }
            };
        </script>
    </body>
    </html>
    """)



