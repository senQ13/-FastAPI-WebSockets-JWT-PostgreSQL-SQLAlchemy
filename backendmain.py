from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
@app.get("/webhook_info")
async def webhook_info():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"error": "Токен не задан"}

    api_url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    async with httpx.AsyncClient() as client:
        response = await client.get(api_url)
        return response.json()
@app.post("/set/telegramwebhook")
async def set_webhook():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise HTTPException(status_code=500 , detail="Error env value TELEGRAM_TOKEN")
    webhook_url = "https://fastapi-websockets-jwt-postgresql-ydp5.onrender.com/webhook/telegram"
    api_url = f"https://api.telegram.org/bot{token}/setWebhook"
    async with httpx.AsyncClient() as client:
        response = await client.post(api_url , json={"url" : webhook_url})
        result = response.json()
    if result.get("ok"):
        return {"message": "Вебхук установлен", "url": webhook_url}
    else:
        raise HTTPException(500 , result.get("description" , "Ошибка"))
@app.post("/webhook/telegram")
async def webhook_telegram(request: Request):
    try:
        data = await request.json()
        if "message" not in data:
            return {"ok" : True}
        if "text" not in data["message"]:
            return {"ok" : True}
        message = data["message"]["text"]
        nameofuser = data["message"]["from"].get("first_name", "Аноним")
        if 1 in active_collections:
            for websocket in active_collections[1]:
                await websocket.send_text(f"Вебхук : {message} от {nameofuser}")
            return{"ok" : True}

    except Exception as e:
        print(f"ошибка {e}")
        return{"ok" : False}


@app.get("/")
async def root():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Чат | Graphite</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
                background: #121212;
                height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
            }

            /* Контейнер чата */
            .chat-container {
                width: 100%;
                max-width: 1000px;
                height: 90vh;
                background: #1e1e1e;
                border-radius: 20px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }

            /* Шапка */
            .chat-header {
                background: #2a2a2a;
                padding: 16px 20px;
                border-bottom: 1px solid #3a3a3a;
                display: flex;
            justify-content: space-between;
                align-items: center;
                flex-wrap: wrap;
                gap: 10px;
            }

            .chat-header h1 {
                color: #e0e0e0;
                font-size: 1.4rem;
                font-weight: 600;
                letter-spacing: -0.3px;
            }

            .chat-header .room-controls {
                display: flex;
                gap: 10px;
                align-items: center;
                background: #1e1e1e;
                padding: 5px 12px;
                border-radius: 12px;
            }

            .chat-header select, .chat-header button {
                background: #3a3a3a;
                border: none;
                color: #e0e0e0;
                padding: 8px 14px;
                border-radius: 10px;
                font-size: 0.85rem;
                cursor: pointer;
                transition: all 0.2s;
            }

            .chat-header select:hover, .chat-header button:hover {
                background: #4a4a4a;
            }

            .chat-header button:active {
                transform: scale(0.98);
            }

            /* Область сообщений */
            .messages-area {
                flex: 1;
                overflow-y: auto;
                padding: 20px;
                display: flex;
                flex-direction: column;
                gap: 12px;
                background: #1a1a1a;
            }

            /* Сообщения */
            .message {
                max-width: 70%;
                padding: 10px 14px;
                border-radius: 18px;
                font-size: 0.9rem;
                line-height: 1.4;
                word-wrap: break-word;
                animation: fadeIn 0.2s ease;
            }

            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(5px); }
                to { opacity: 1; transform: translateY(0); }
            }

            .message.my {
                background: #2b5d8c;
                color: white;
                align-self: flex-end;
                border-bottom-right-radius: 4px;
            }

            .message.other {
                background: #2a2a2a;
                color: #e0e0e0;
                align-self: flex-start;
                border-bottom-left-radius: 4px;
                border: 1px solid #3a3a3a;
            }

            .message .sender {
                font-size: 0.7rem;
                font-weight: 600;
                margin-bottom: 4px;
                color: #888;
            }

            .message.my .sender {
                color: #aac9e4;
            }

            /* Аудио блок */
            .message audio {
                margin-top: 6px;
                max-width: 200px;
                border-radius: 20px;
            }

            /* Панель ввода */
            .input-panel {
                background: #2a2a2a;
                padding: 16px 20px;
                border-top: 1px solid #3a3a3a;
                display: flex;
                gap: 12px;
                align-items: center;
                flex-wrap: wrap;
            }

            .input-panel input {
                flex: 1;
                background: #1e1e1e;
                border: 1px solid #3a3a3a;
                color: #e0e0e0;
                padding: 12px 16px;
                border-radius: 25px;
                font-size: 0.9rem;
                outline: none;
                transition: border 0.2s;
            }

            .input-panel input:focus {
                border-color: #2b5d8c;
            }

            .input-panel button {
                background: #3a3a3a;
                border: none;
                color: #e0e0e0;
                padding: 12px 20px;
                border-radius: 25px;
                font-size: 0.9rem;
                cursor: pointer;
                transition: all 0.2s;
                display: inline-flex;
                align-items: center;
                gap: 8px;
            }

            .input-panel button:hover {
                background: #4a4a4a;
            }

            .input-panel button.record {
                background: #8c2b2b;
            }

            .input-panel button.record:hover {
                background: #a33;
            }

            .input-panel button.stop {
                background: #555;
            }

            /* Форма авторизации */
            .auth-container {
                background: #1e1e1e;
                padding: 30px;
                border-radius: 20px;
                width: 320px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            }

            .auth-container h2 {
                color: #e0e0e0;
                margin-bottom: 20px;
                font-weight: 500;
            }

            .auth-container input {
                width: 100%;
                background: #2a2a2a;
                border: 1px solid #3a3a3a;
                color: #e0e0e0;
                padding: 12px;
                border-radius: 12px;
                margin-bottom: 12px;
                font-size: 0.9rem;
                outline: none;
            }

            .auth-container input:focus {
                border-color: #2b5d8c;
            }

            .auth-container button {
                width: 100%;
                background: #2b5d8c;
                border: none;
                color: white;
                padding: 12px;
                border-radius: 12px;
                font-size: 0.9rem;
                cursor: pointer;
                margin-top: 8px;
                transition: background 0.2s;
            }

            .auth-container button:hover {
                background: #1e4a70;
            }

            .auth-container .error {
                color: #d66;
                margin-top: 12px;
                font-size: 0.8rem;
            }

            /* Скроллбар */
            .messages-area::-webkit-scrollbar {
                width: 6px;
            }

            .messages-area::-webkit-scrollbar-track {
                background: #1e1e1e;
            }

            .messages-area::-webkit-scrollbar-thumb {
                background: #3a3a3a;
                border-radius: 4px;
            }

            .hidden {
                display: none;
            }
        </style>
    </head>
    <body>
        <div id="authContainer" class="auth-container">
            <h2>✧ Вход в чат ✧</h2>
            <input type="text" id="username" placeholder="Имя пользователя">
            <input type="password" id="password" placeholder="Пароль">
            <button id="regBtn">📝 Зарегистрироваться</button>
            <button id="loginBtn">🔑 Войти</button>
            <div id="authError" class="error"></div>
        </div>

        <div id="chatContainer" class="chat-container hidden">
            <div class="chat-header">
                <h1>💬 Graphite Chat</h1>
                <div class="room-controls">
                    <select id="roomSelect">
                        <option value="1">🏠 general</option>
                        <option value="2">🎲 random</option>
                        <option value="3">💻 tech</option>
                    </select>
                    <button id="joinRoomBtn">🔄 Перейти</button>
                    <button id="logoutBtn">🚪 Выйти</button>
                </div>
            </div>
            <div class="messages-area" id="messagesArea">
                <div style="text-align: center; color: #555; padding: 40px;">💬 Сообщения будут здесь</div>
            </div>
            <div class="input-panel">
                <input type="text" id="messageInput" placeholder="Сообщение..." autocomplete="off">
                <button id="sendBtn">📤 Отправить</button>
                <button id="recordBtn" class="record">🎤 Запись</button>
                <button id="stopBtn" class="stop" style="display: none;">⏹ Стоп</button>
            </div>
        </div>

        <script>
            let ws = null;
            let token = null;
            let mediaRecorder = null;
            let audioChunks = [];
            let isRecording = false;
            let currentRoomId = "1";

            const authContainer = document.getElementById('authContainer');
            const chatContainer = document.getElementById('chatContainer');
            const messagesArea = document.getElementById('messagesArea');
            const usernameInput = document.getElementById('username');
            const passwordInput = document.getElementById('password');
            const authError = document.getElementById('authError');
            const roomSelect = document.getElementById('roomSelect');
            const messageInput = document.getElementById('messageInput');

            async function apiCall(url, data) {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const json = await res.json();
                if (!res.ok) throw new Error(json.detail || 'Ошибка');
                return json;
            }

            document.getElementById('regBtn').onclick = async () => {
                try {
                    await apiCall('/register', { username: usernameInput.value, password: passwordInput.value });
                    authError.innerText = '✅ Регистрация успешна! Теперь войдите.';
                    authError.style.color = '#4caf50';
                } catch(e) {
                    authError.innerText = '❌ ' + e.message;
                    authError.style.color = '#d66';
                }
            };

            document.getElementById('loginBtn').onclick = async () => {
                try {
                    const data = await apiCall('/login', { username: usernameInput.value, password: passwordInput.value });
                    token = data.token;
                    authContainer.classList.add('hidden');
                    chatContainer.classList.remove('hidden');
                    connectWebSocket();
                } catch(e) {
                    authError.innerText = '❌ ' + e.message;
                    authError.style.color = '#d66';
                }
            };

            document.getElementById('joinRoomBtn').onclick = () => {
                if (ws) ws.close();
                connectWebSocket();
            };

            document.getElementById('logoutBtn').onclick = () => {
                if (ws) ws.close();
                token = null;
                authContainer.classList.remove('hidden');
                chatContainer.classList.add('hidden');
                messagesArea.innerHTML = '<div style="text-align: center; color: #555; padding: 40px;">💬 Сообщения будут здесь</div>';
            };

            function connectWebSocket() {
                currentRoomId = roomSelect.value;
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${window.location.host}/ws/${currentRoomId}?token=${token}`;
                ws = new WebSocket(wsUrl);

                ws.onopen = () => {
                    console.log('WebSocket connected to room', currentRoomId);
                    loadHistory();
                };

                ws.onmessage = (e) => {
                    addMessageToChat(e.data, false);
                };

                ws.onerror = (err) => {
                    console.error('WebSocket error:', err);
                };
            }

            async function loadHistory() {
                try {
                    const res = await fetch(`/history/${currentRoomId}?token=${token}`);
                    const msgs = await res.json();
                    messagesArea.innerHTML = '';
                    for (let msg of msgs) {
                        addMessageToChat(msg.text, false, msg.username);
                    }
                } catch(e) {
                    console.error('History error:', e);
                }
            }

            function addMessageToChat(text, isMy = false, sender = null) {
                const msgDiv = document.createElement('div');
                msgDiv.className = `message ${isMy ? 'my' : 'other'}`;

                if (!isMy && sender) {
                    const senderSpan = document.createElement('div');
                    senderSpan.className = 'sender';
                    senderSpan.innerText = sender;
                    msgDiv.appendChild(senderSpan);
                }

                // Проверка на голосовое сообщение
                try {
                    const parsed = JSON.parse(text);
                    if (parsed.type === 'voice' && parsed.url) {
                        const audio = document.createElement('audio');
                        audio.controls = true;
                        audio.src = parsed.url;
                        msgDiv.appendChild(audio);
                    } else {
                        msgDiv.appendChild(document.createTextNode(parsed.text || text));
                    }
                } catch {
                    msgDiv.appendChild(document.createTextNode(text));
                }

                messagesArea.appendChild(msgDiv);
                msgDiv.scrollIntoView({ behavior: 'smooth', block: 'end' });
            }

            async function uploadAudio(blob) {
                const formData = new FormData();
                formData.append('file', blob, 'voice.webm');
                const res = await fetch('/uploadaudio', { method: 'POST', body: formData });
                if (!res.ok) throw new Error('Upload failed');
                const data = await res.json();
                ws.send(JSON.stringify({ type: 'voice', url: data.url }));
                addMessageToChat('🎤 Голосовое сообщение', true);
            }

            const recordBtn = document.getElementById('recordBtn');
            const stopBtn = document.getElementById('stopBtn');

            recordBtn.onclick = async () => {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
                mediaRecorder.onstop = () => {
                    const blob = new Blob(audioChunks, { type: 'audio/webm' });
                    uploadAudio(blob);
                    stream.getTracks().forEach(t => t.stop());
                };
                mediaRecorder.start();
                isRecording = true;
                recordBtn.style.display = 'none';
                stopBtn.style.display = 'inline-block';
            };

            stopBtn.onclick = () => {
                if (mediaRecorder && isRecording) {
                    mediaRecorder.stop();
                    isRecording = false;
                    recordBtn.style.display = 'inline-block';
                    stopBtn.style.display = 'none';
                }
            };

            document.getElementById('sendBtn').onclick = () => {
                const text = messageInput.value.trim();
                if (text && ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: 'text', text: text }));
                    addMessageToChat(text, true);
                    messageInput.value = '';
                }
            };

            messageInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') document.getElementById('sendBtn').click();
            });
        </script>
    </body>
    </html>
    """)


