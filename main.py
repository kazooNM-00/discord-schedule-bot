import os
import asyncio
import sqlite3
from typing import List
from contextlib import asynccontextmanager

import discord
from discord import app_commands
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

# ==========================================
# 1. データベース設定
# ==========================================
DB_PATH = "schedule.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            creator_id TEXT,
            dates TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS answers (
            event_id TEXT,
            user_id TEXT,
            user_name TEXT,
            date_str TEXT,
            status TEXT,
            PRIMARY KEY (event_id, user_id, date_str)
        )
    ''')
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# 2. Discord Bot 設定
# ==========================================
class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

@bot.tree.command(name="create", description="日程調整イベントを作成します")
async def create_event(interaction: discord.Interaction, title: str):
    # チャットコマンドでイベントを作成
    event_id = str(interaction.id)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO events (id, title) VALUES (?, ?)", (event_id, title))
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"📅 イベント「**{title}**」を作成しました！\nボイスチャンネルのアクティビティから回答を開いてください。"
    )

# ==========================================
# 3. FastAPI（Web画面用API）設定
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # アプリ起動時にDB初期化とBotの非同期起動を行う
    init_db()
    BOT_TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
    asyncio.create_task(bot.start(BOT_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="public"), name="static")

class SubmitAnswerRequest(BaseModel):
    event_id: str
    user_id: str
    user_name: str
    answers: dict

@app.get("/")
async def read_index():
    with open("public/index.html", "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)

@app.get("/api/event/{event_id}")
async def get_event(event_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    event = cursor.fetchone()
    if not event:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    
    cursor.execute("SELECT user_id, user_name, date_str, status FROM answers WHERE event_id = ?", (event_id,))
    answers_rows = cursor.fetchall()
    conn.close()
    
    return {
        "event_id": event["id"],
        "title": event["title"],
        "answers": [dict(row) for row in answers_rows]
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)