import os
import discord
from discord import app_commands
from discord.ext import commands
import datetime
from fastapi import FastAPI
import uvicorn
import asyncio

# --- 1. Web サーバー (FastAPI) 設定 ---
app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "bot is running"}

# --- 2. Discord Bot 設定 ---
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# イベントデータを一時保存する辞書 (簡易メモリ保持)
events_data = {}

# --- 3. UI コンポーネント (モーダル・ビュー) ---

# 回答入力用モーダル
class AnswerModal(discord.ui.Modal, title="日程調整の回答"):
    def __init__(self, event_id: str, dates: list, symbols: list):
        super().__init__()
        self.event_id = event_id
        self.dates = dates
        self.symbols = symbols
        
        self.name_input = discord.ui.TextInput(
            label="お名前",
            placeholder="あなたの名前を入力してください",
            required=True
        )
        self.add_item(self.name_input)
        
        # 日付ごとの回答欄
        self.date_inputs = []
        symbols_str = "/".join(self.symbols)
        for d in self.dates[:4]: # Discordの制限のため最大4日分まで表示
            inp = discord.ui.TextInput(
                label=f"{d} の回答 ({symbols_str})",
                placeholder=f"例: {self.symbols[0]}",
                required=True,
                max_length=5
            )
            self.date_inputs.append(inp)
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        user_name = self.name_input.value
        answers = [inp.value for inp in self.date_inputs]
        
        ans_text = "\n".join([f"・{d}: {a}" for d, a in zip(self.dates, answers)])
        await interaction.response.send_message(
            f"**{user_name}** さんの回答を受け付けました！\n{ans_text}",
            ephemeral=True
        )

# イベントメッセージに付く「回答・閲覧」ボタン
class EventView(discord.ui.View):
    def __init__(self, event_id: str):
        super().__init__(timeout=None)
        self.event_id = event_id

    @discord.ui.button(label="回答・編集する", style=discord.ButtonStyle.primary, custom_id="answer_btn")
    async def answer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = events_data.get(self.event_id)
        if not data:
            await interaction.response.send_message("イベント情報が見つかりませんでした。", ephemeral=True)
            return
        
        modal = AnswerModal(self.event_id, data["dates"], data["symbols"])
        await interaction.response.send_modal(modal)

# Step 3: タイトル・説明文を入力するモーダル
class EventDetailModal(discord.ui.Modal, title="イベント詳細の入力"):
    def __init__(self, start_date: str, end_date: str, symbols: list):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date
        self.symbols = symbols

        self.event_title = discord.ui.TextInput(
            label="イベントタイトル",
            placeholder="例: 9月度 定例ミーティング",
            required=True
        )
        self.event_desc = discord.ui.TextInput(
            label="説明（任意）",
            style=discord.TextStyle.paragraph,
            placeholder="例: 空いている時間を回答してください。",
            required=False
        )
        self.add_item(self.event_title)
        self.add_item(self.event_desc)

    async def on_submit(self, interaction: discord.Interaction):
        # 開始日から終了日までの日付リストを生成
        s_date = datetime.datetime.strptime(self.start_date, "%Y-%m-%d")
        e_date = datetime.datetime.strptime(self.end_date, "%Y-%m-%d")
        
        date_list = []
        curr = s_date
        while curr <= e_date:
            date_list.append(curr.strftime("%m/%d(%a)"))
            curr += datetime.timedelta(days=1)

        event_id = str(interaction.id)
        events_data[event_id] = {
            "title": self.event_title.value,
            "desc": self.event_desc.value,
            "dates": date_list,
            "symbols": self.symbols
        }

        embed = discord.Embed(
            title=f"📅 {self.event_title.value}",
            description=self.event_desc.value or "下のボタンから回答してください。",
            color=discord.Color.blue()
        )
        embed.add_field(name="候補期間", value=f"{self.start_date} ～ {self.end_date}", inline=False)
        embed.add_field(name="回答記号", value=" / ".join(self.symbols), inline=False)

        await interaction.response.send_message(
            content="新しい日程調整イベントが作成されました！",
            embed=embed,
            view=EventView(event_id)
        )

# Step 2: 終了日と記号を選択するビュー
class EndDateSelectView(discord.ui.View):
    def __init__(self, start_date: str):
        super().__init__(timeout=180)
        self.start_date = start_date
        self.selected_end_date = None
        self.selected_symbols = ["〇", "△", "×"]

        # 終了日選択 (開始日から7日間)
        s_date = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        options = []
        for i in range(7):
            d = s_date + datetime.timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            options.append(discord.SelectOption(label=d_str, value=d_str))

        end_select = discord.ui.Select(
            placeholder="イベント候補終了日を選択してください",
            options=options
        )
        end_select.callback = self.end_date_callback
        self.add_item(end_select)

    async def end_date_callback(self, interaction: discord.Interaction):
        self.selected_end_date = interaction.data["values"][0]
        modal = EventDetailModal(self.start_date, self.selected_end_date, self.selected_symbols)
        await interaction.response.send_modal(modal)

# Step 1: 開始日を選択するビュー
class StartDateSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        today = datetime.date.today()
        options = []
        for i in range(7):
            d = today + datetime.timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            options.append(discord.SelectOption(label=d_str, value=d_str))

        start_select = discord.ui.Select(
            placeholder="イベント候補開始日を選択してください",
            options=options
        )
        start_select.callback = self.start_date_callback
        self.add_item(start_select)

    async def start_date_callback(self, interaction: discord.Interaction):
        start_date = interaction.data["values"][0]
        view = EndDateSelectView(start_date)
        await interaction.response.send_message(
            f"開始日: **{start_date}** を選択しました。\n次に**終了日**を選択してください：",
            view=view,
            ephemeral=True
        )

# --- 4. スラッシュコマンド定義 ---

@tree.command(name="create", description="新しい日程調整イベントを作成します")
async def create(interaction: discord.Interaction):
    # 引数なしで実行し、対話型で開始日選択からスタート
    view = StartDateSelectView()
    await interaction.response.send_message(
        "イベントの**候補開始日**を選択してください：",
        view=view,
        ephemeral=True
    )

GUILD_ID = 1494540447572557964  # ←ここにコピーしたサーバーIDを入れる

@client.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    # グローバル定義をこのサーバー専用にコピーして即時同期
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    print(f"Logged in as {client.user} (Guild synced successfully!)")

# --- 5. 起動処理 ---
async def main():
    port = int(os.environ.get("PORT", 10000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN 環境変数が設定されていません。")

    await asyncio.gather(
        server.serve(),
        client.start(token)
    )

if __name__ == "__main__":
    asyncio.run(main())
