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
@app.get("/set_telegram_webhook")
@app.post("/set_telegram_webhook")
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
            return{"ok": "True"}
        if "text" not in data["message"]:
            return {"ok": "True"}
        message = data["message"]["text"]
        account_name = data["message"]["from"].get("first_name" , "Аноним")

        if 1 in active_collections:
            for i in active_collections[1]:
                await i.send_text(f"ПРИШЕЛ ВЕБХУК {message} от  {account_name}")
        return {"ok": "True"}
    except Exception as e:
        print(f"ошибка {e}")
        return {"ok": "False"}
@app.get("/test-api")
async def test_api():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.github.com/users/defunkt")
        return response.json()
giga_cache = {
    "access_token" : None ,
    "created_at" : None
}
async def get_giga_token():
    global giga_cache
    if giga_cache["access_token"] and giga_cache["created_at"]:
        if datetime.utcnow() < giga_cache["created_at"]:
            return giga_cache["access_token"]
    basic_auth = os.getenv("GIGA_BASIC_AUTH")
    if not basic_auth:
        raise Exception("Токен не задан в .env")
    async with httpx.AsyncClient() as client:
        response = await client.post("https://ngw.devices.sberbank.ru:9443/api/v2/oauth" ,
                                     data = {"scope" : "GIGACHAT_API_PERS"},
                                     headers = {"Content-Type" : "application/x-www-form-urlencoded",
                                                "Accept" : "application/json" ,
                                                "RqUID" : str(uuid.uuid4()),
                                                "Authorization" : f"Basic {basic_auth}"},
                                     verify = False,
                                     )
        if response.status_code != 200:
            raise Exception(f"Ошибка {response.text}")
        data = response.json()
        giga_cache["access_token"] = data["access_token"]
        giga_cache["created_at"] = datetime.utcnow() + timedelta(seconds=data.get("expires_at", 1800))
        return giga_cache["access_token"]
async def ask_giga(prompt : str) -> str:
    token = await get_giga_token()
    async with httpx.AsyncClient() as client:
        response = await client.post("https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                                     headers = {"Authorization" : f"Bearer {token}",
                                                "Content-Type" : "application/json"
                                                },
                                     json = {
                                         "model" : "GigaChat",
                                         "messages": [{"role": "user", "content": prompt}],
                                         "temperature" : 0.7,
                                         "max_tokens" : 500
                                            },
                                     timeout = 30,
                                     verify = False
                                     )
        if response.status_code != 200:
            raise Exception(f"Ошибка гигачат {response.text}")
        data = response.json()
        return data["choices"][0]["message"]["content"]
@app.post("/chat/ai")
async def get_ai(request: Request):
    try:
        body = await request.json()
        prompt = body.get("prompt")
        room_id = body.get("room_id" , 1)
        if not prompt:
            raise HTTPException(400 , "Нету запроса")
        if room_id in active_collections:
            for i in active_collections[room_id]:
                await i.send_text(f"AI печатает...")
        answer = await ask_giga(prompt)
        if room_id in active_collections:
            for ws in active_collections[room_id]:
                await ws.send_text(f"AI : {answer}")

        return{"answer" : answer}
    except Exception as e:
        print(f"AI ошибка: {e}")
        raise HTTPException(500, f"Ошибка AI: {str(e)}")
@app.get("/")
async def root():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Чат | Telegram</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        /* Общий сброс */
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0a0a0a;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0;
        }

        /* Контейнер как окно Telegram */
        .app {
            width: 100%;
            max-width: 900px;
            height: 95vh;
            background: #1f2c33;
            border-radius: 24px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            box-shadow: 0 20px 60px rgba(0,0,0,0.8);
        }

        /* Шапка (как у Telegram) */
        .header {
            background: #1f2c33;
            padding: 16px 20px;
            border-bottom: 1px solid #2c3b43;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        .header h1 {
            color: #e1e9ee;
            font-size: 1.3rem;
            font-weight: 600;
            letter-spacing: 0px;
        }
        .header-controls {
            display: flex;
            gap: 10px;
            align-items: center;
            background: #2c3b43;
            padding: 5px 12px;
            border-radius: 30px;
        }
        .header-controls select,
        .header-controls button {
            background: transparent;
            border: none;
            color: #e1e9ee;
            padding: 6px 12px;
            border-radius: 30px;
            font-size: 0.85rem;
            cursor: pointer;
            transition: 0.2s;
        }
        .header-controls select:hover,
        .header-controls button:hover {
            background: #3e4f59;
        }

        /* Область сообщений — как чат Telegram */
        .messages-area {
            flex: 1;
            overflow-y: auto;
            padding: 20px 20px 10px 20px;
            background: #0e1621;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .messages-area::-webkit-scrollbar {
            width: 6px;
        }
        .messages-area::-webkit-scrollbar-thumb {
            background: #3e4f59;
            border-radius: 10px;
        }

        /* Сообщение */
        .message {
            max-width: 75%;
            padding: 8px 14px;
            border-radius: 18px;
            font-size: 0.95rem;
            line-height: 1.4;
            word-wrap: break-word;
            animation: fade 0.15s ease;
        }
        @keyframes fade {
            from { opacity: 0; transform: translateY(4px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .message.my {
            background: #2b5278;
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 4px;
        }
        .message.other {
            background: #1f2c33;
            color: #e1e9ee;
            align-self: flex-start;
            border-bottom-left-radius: 4px;
        }
        .message .sender {
            font-size: 0.7rem;
            font-weight: 600;
            color: #6f8b9c;
            margin-bottom: 3px;
        }
        .message.my .sender {
            color: #9ab8d9;
        }

        /* Аудио (голосовые) */
        .message audio {
            max-width: 200px;
            border-radius: 20px;
            margin-top: 4px;
        }

        /* Панель ввода — как в Telegram */
        .input-panel {
            background: #1f2c33;
            padding: 12px 16px;
            border-top: 1px solid #2c3b43;
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        .input-panel input {
            flex: 1;
            background: #2c3b43;
            border: none;
            color: #e1e9ee;
            padding: 12px 16px;
            border-radius: 30px;
            font-size: 0.95rem;
            outline: none;
        }
        .input-panel input::placeholder {
            color: #6f8b9c;
        }
        .input-panel button {
            background: #2c3b43;
            border: none;
            color: #e1e9ee;
            padding: 10px 18px;
            border-radius: 30px;
            font-size: 0.9rem;
            cursor: pointer;
            transition: 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .input-panel button:hover {
            background: #3e4f59;
        }
        .input-panel .record {
            background: #4a3b3b;
        }
        .input-panel .record:hover {
            background: #5f4a4a;
        }

        /* Кнопка AI — голубая, заметная */
        .input-panel .ai-btn {
            background: #2b5278;
            font-weight: 500;
        }
        .input-panel .ai-btn:hover {
            background: #3b6a90;
        }

        /* Форма авторизации */
        .auth-container {
            background: #1f2c33;
            padding: 40px 30px;
            border-radius: 24px;
            width: 340px;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0,0,0,0.8);
        }
        .auth-container h2 {
            color: #e1e9ee;
            margin-bottom: 24px;
            font-weight: 500;
        }
        .auth-container input {
            width: 100%;
            background: #2c3b43;
            border: none;
            color: #e1e9ee;
            padding: 14px;
            border-radius: 16px;
            margin-bottom: 14px;
            font-size: 0.95rem;
            outline: none;
        }
        .auth-container button {
            width: 100%;
            background: #2b5278;
            border: none;
            color: white;
            padding: 14px;
            border-radius: 16px;
            font-size: 0.95rem;
            cursor: pointer;
            margin-top: 8px;
            transition: 0.2s;
        }
        .auth-container button:hover {
            background: #3b6a90;
        }
        .auth-container .error {
            color: #d66;
            margin-top: 12px;
            font-size: 0.85rem;
        }
        .hidden {
            display: none !important;
        }

        /* Адаптив */
        @media (max-width: 600px) {
            .app { height: 100vh; border-radius: 0; }
            .header h1 { font-size: 1rem; }
            .message { max-width: 85%; }
        }
    </style>
</head>
<body>

<div id="authContainer" class="auth-container">
    <h2>✧ Вход в чат</h2>
    <input type="text" id="username" placeholder="Имя пользователя">
    <input type="password" id="password" placeholder="Пароль">
    <button id="regBtn">📝 Зарегистрироваться</button>
    <button id="loginBtn">🔑 Войти</button>
    <div id="authError" class="error"></div>
</div>

<div id="chatContainer" class="app hidden">
    <!-- ШАПКА -->
    <div class="header">
        <h1>💬 Telegram Chat</h1>
        <div class="header-controls">
            <select id="roomSelect">
                <option value="1">🏠 general</option>
                <option value="2">🎲 random</option>
                <option value="3">💻 tech</option>
            </select>
            <button id="joinRoomBtn">↻ Перейти</button>
            <button id="logoutBtn">🚪 Выйти</button>
        </div>
    </div>

    <!-- ОБЛАСТЬ СООБЩЕНИЙ -->
    <div class="messages-area" id="messagesArea">
        <div style="text-align:center; color:#4a5f6b; padding:40px;">💬 Сообщения будут здесь</div>
    </div>

    <!-- ПАНЕЛЬ ВВОДА -->
    <div class="input-panel">
        <input type="text" id="messageInput" placeholder="Сообщение..." autocomplete="off">
        <button id="sendBtn">📤</button>
        <button id="recordBtn" class="record">🎤</button>
        <button id="stopBtn" class="record" style="display:none;">⏹</button>
        <button id="aiBtn" class="ai-btn">🤖 AI</button>
    </div>
</div>

<script>
// ====================================================
// ВСЯ ЛОГИКА (та же, что работала, но с новыми ID)
// ====================================================

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

// Функция добавления сообщения в чат
function addMessageToChat(text, isMy = false, sender = null) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${isMy ? 'my' : 'other'}`;

    if (!isMy && sender) {
        const senderSpan = document.createElement('div');
        senderSpan.className = 'sender';
        senderSpan.textContent = sender;
        msgDiv.appendChild(senderSpan);
    }

    // Проверка на голосовое
    try {
        const parsed = JSON.parse(text);
        if (parsed.type === 'voice' && parsed.url) {
            const audio = document.createElement('audio');
            audio.controls = true;
            audio.src = parsed.url;
            msgDiv.appendChild(audio);
        } else {
            msgDiv.textContent = parsed.text || text;
        }
    } catch {
        msgDiv.textContent = text;
    }

    messagesArea.appendChild(msgDiv);
    messagesArea.scrollTop = messagesArea.scrollHeight;
}

// WebSocket
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

// История
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

// Регистрация
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

// Логин
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

// Переключение комнаты
document.getElementById('joinRoomBtn').onclick = () => {
    if (ws) ws.close();
    connectWebSocket();
};

// Выход
document.getElementById('logoutBtn').onclick = () => {
    if (ws) ws.close();
    token = null;
    authContainer.classList.remove('hidden');
    chatContainer.classList.add('hidden');
    messagesArea.innerHTML = '<div style="text-align:center; color:#4a5f6b; padding:40px;">💬 Сообщения будут здесь</div>';
};

// Отправка текста
document.getElementById('sendBtn').onclick = () => {
    const text = messageInput.value.trim();
    if (text && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'text', text: text }));
        addMessageToChat(text, true);
        messageInput.value = '';
    }
};

// Голос
document.getElementById('recordBtn').onclick = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
    mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunks, { type: 'audio/webm' });
        const formData = new FormData();
        formData.append('file', blob, 'voice.webm');
        const res = await fetch('/uploadaudio', { method: 'POST', body: formData });
        if (!res.ok) throw new Error('Upload failed');
        const data = await res.json();
        ws.send(JSON.stringify({ type: 'voice', url: data.url }));
        stream.getTracks().forEach(t => t.stop());
    };
    mediaRecorder.start();
    isRecording = true;
    document.getElementById('recordBtn').style.display = 'none';
    document.getElementById('stopBtn').style.display = 'inline-block';
};

document.getElementById('stopBtn').onclick = () => {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
        isRecording = false;
        document.getElementById('recordBtn').style.display = 'inline-block';
        document.getElementById('stopBtn').style.display = 'none';
    }
};

// ============================================
// ⭐️ НОВОЕ: КНОПКА AI (GigaChat)
// ============================================
document.getElementById('aiBtn').onclick = async () => {
    const question = messageInput.value.trim();
    if (!question) return;

    // Показываем вопрос в чате
    addMessageToChat(question, true);
    messageInput.value = '';

    try {
        const res = await fetch('/chat/ai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: question, room_id: currentRoomId })
        });
        if (!res.ok) throw new Error('AI ошибка');
        // Ответ придёт через WebSocket автоматически
    } catch(e) {
        console.error(e);
        addMessageToChat('❌ AI не ответил', false);
    }
};

// Enter = отправка
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') document.getElementById('sendBtn').click();
});

</script>
</body>
</html>
    """)