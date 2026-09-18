import os
import asyncio
import sqlite3
from datetime import datetime, timedelta

import discord
from discord import app_commands
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# ==========================================
# FastAPI Web サーバーの設定（Discord Activity用）
# ==========================================
app = FastAPI()

# public フォルダ配下の静的ファイル（index.html など）を配信
app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("public/index.html")

# ==========================================
# ヘルパー関数
# ==========================================
def get_valid_days(year, month_str, period):
    month = int(month_str)
    if period == "上旬":
        return range(1, 11)
    elif period == "中旬":
        return range(11, 21)
    else:
        if month in [1, 3, 5, 7, 8, 10, 12]:
            return range(21, 32)
        elif month in [4, 6, 9, 11]:
            return range(21, 31)
        elif month == 2:
            if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
                return range(21, 30)
            else:
                return range(21, 29)
    return range(21, 32)

# ==========================================
# 0. モーダル（タイトル・説明入力）
# ==========================================
class EventInfoModal(discord.ui.Modal, title="イベントの詳細入力"):
    event_title = discord.ui.TextInput(label="イベントのタイトル", placeholder="例: 〇〇会議、定例飲み会など", max_length=45, required=True)
    event_desc = discord.ui.TextInput(label="コメント・説明文（空欄でもOK）", style=discord.TextStyle.paragraph, placeholder="例: 空いている時間を回答してください。", max_length=500, required=False)

    def __init__(self, start_dt, end_dt, date_list, symbols):
        super().__init__()
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.date_list = date_list
        self.symbols = symbols

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        title_text = self.event_title.value
        desc_text = self.event_desc.value if self.event_desc.value else "（説明はありません）"
        dates_str = ",".join(self.date_list)
        creator_id = str(interaction.user.id)

        conn = sqlite3.connect('scheduler.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO events (title, description, creator_id, dates, symbols, notification_time)
            VALUES (?, ?, ?, ?, ?, 'none')
        ''', (title_text, desc_text, creator_id, dates_str, self.symbols))
        conn.commit()
        event_id = cursor.lastrowid
        conn.close()

        view = EventResponseView(event_id=event_id, title=title_text)
        embed = discord.Embed(title=title_text, description=desc_text, color=discord.Color.green())
        embed.add_field(name="📅 調整対象期間", value=f"**{self.start_dt.strftime('%Y/%m/%d')} 〜 {self.end_dt.strftime('%Y/%m/%d')}**", inline=False)
        embed.add_field(name="✨ 回答に使用する記号", value=f"` {self.symbols.replace(',', ' ` ` ')} `", inline=True)
        embed.add_field(name="🔢 候補日数", value=f"計 {len(self.date_list)} 日間", inline=True)
        embed.set_footer(text=f"作成者: {interaction.user.display_name}")

        await interaction.followup.send(content="📢 新しい日程調整が作成されました！メンバーは以下から回答してください。", embed=embed, view=view, ephemeral=False)

# ==========================================
# 1. 開始日選択 UI
# ==========================================
class StartDateSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        now = datetime.now()
        self.current_year = now.year
        self.start_year = now.year
        self.start_month = f"{now.month:02d}"
        
        if now.day <= 10:
            self.start_period = "上旬"
        elif now.day <= 20:
            self.start_period = "中旬"
        else:
            self.start_period = "下旬"
        self.start_day = f"{now.day:02d}"

        start_ym_options = []
        for y in range(self.current_year, self.current_year + 3):
            for m in range(1, 13):
                if y == self.current_year and m < now.month:
                    continue
                label = f"{y}年 {m}月"
                value = f"{y}-{m:02d}"
                start_ym_options.append(discord.SelectOption(label=label, value=value))

        self.children[0].options = start_ym_options[:24]
        self.setup_initial_defaults()

    def setup_initial_defaults(self):
        current_ym = f"{self.start_year}-{self.start_month}"
        for option in self.children[0].options:
            option.default = (option.value == current_ym)
        for option in self.children[1].options:
            option.default = (option.value == self.start_period)
            
        days = get_valid_days(self.start_year, self.start_month, self.start_period)
        self.children[2].options = [discord.SelectOption(label=f"{i}日", value=f"{i:02d}") for i in days]
        for option in self.children[2].options:
            option.default = (option.value == self.start_day)

    @discord.ui.select(placeholder="📅 【開始】の『年 と 月』を選んでください", options=[])
    async def select_year_month(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        ym_split = select.values[0].split("-")
        self.start_year = int(ym_split[0])
        self.start_month = ym_split[1]
        await self.update_view(interaction)

    @discord.ui.select(placeholder="⏳ 【開始】の『時期』を選んでください", options=[
        discord.SelectOption(label="上旬 (1日〜10日)", value="上旬"),
        discord.SelectOption(label="中旬 (11日〜20日)", value="中旬"),
        discord.SelectOption(label="下旬 (21日〜月末)", value="下旬"),
    ])
    async def select_period(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self.start_period = select.values[0]
        await self.update_view(interaction)

    @discord.ui.select(placeholder="🔢 【開始】の『日』を選んでください", options=[])
    async def select_day(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self.start_day = select.values[0]

    async def update_view(self, interaction: discord.Interaction):
        current_ym = f"{self.start_year}-{self.start_month}"
        for option in self.children[0].options:
            option.default = (option.value == current_ym)
        for option in self.children[1].options:
            option.default = (option.value == self.start_period)

        days = get_valid_days(self.start_year, self.start_month, self.start_period)
        self.children[2].options = [discord.SelectOption(label=f"{i}日", value=f"{i:02d}") for i in days]
        if int(self.start_day) not in days:
            self.start_day = f"{list(days)[0]:02d}"
        for option in self.children[2].options:
            option.default = (option.value == self.start_day)

        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="次に【終了日・記号】を選ぶ ➡️", style=discord.ButtonStyle.primary, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        next_view = EndDateSelectView(self.start_year, self.start_month, self.start_day)
        await interaction.response.edit_message(content=f"🟢 開始日を「{self.start_year}/{self.start_month}/{self.start_day}」に設定しました。\n次に【終了日】と【回答に使う記号】を選択してください：", view=next_view)

# ==========================================
# 2. 終了日と記号選択 UI
# ==========================================
class EndDateSelectView(discord.ui.View):
    def __init__(self, start_year, start_month, start_day):
        super().__init__(timeout=180)
        self.start_year = start_year
        self.start_month = start_month
        self.start_day = start_day
        self.end_year = start_year
        self.end_month = start_month  
        
        start_day_int = int(start_day)
        if start_day_int <= 10:
            self.end_period = "上旬"
        elif start_day_int <= 20:
            self.end_period = "中旬"
        else:
            self.end_period = "下旬"
        self.end_day = start_day
        self.selected_symbols = "〇,△,×"

        year_month_options = []
        for y in range(self.start_year, self.start_year + 3):
            for m in range(1, 13):
                if y == self.start_year and m < int(self.start_month):
                    continue
                label = f"{y}年 {m}月"
                value = f"{y}-{m:02d}"
                year_month_options.append(discord.SelectOption(label=label, value=value))
        
        self.children[0].options = year_month_options[:24]
        self.setup_initial_defaults()

    def setup_initial_defaults(self):
        current_ym = f"{self.end_year}-{self.end_month}"
        for option in self.children[0].options:
            option.default = (option.value == current_ym)
        for option in self.children[1].options:
            option.default = (option.value == self.end_period)
        for option in self.children[3].options:
            option.default = (option.value == self.selected_symbols)

        days = get_valid_days(self.end_year, self.end_month, self.end_period)
        self.children[2].options = [discord.SelectOption(label=f"{i}日", value=f"{i:02d}") for i in days]
        for option in self.children[2].options:
            option.default = (option.value == self.end_day)

    @discord.ui.select(placeholder="🏁 【終了】の『年 と 月』を選んでください", options=[])
    async def select_end_year_month(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        ym_split = select.values[0].split("-")
        self.end_year = int(ym_split[0])
        self.end_month = ym_split[1]
        await self.update_view(interaction)

    @discord.ui.select(placeholder="⏳ 【終了】の『時期』を選んでください", options=[
        discord.SelectOption(label="上旬 (1日〜10日)", value="上旬"),
        discord.SelectOption(label="中旬 (11日〜20日)", value="中旬"),
        discord.SelectOption(label="下旬 (21日〜月末)", value="下旬"),
    ])
    async def select_end_period(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self.end_period = select.values[0]
        await self.update_view(interaction)

    @discord.ui.select(placeholder="🔢 【終了】の『日』を選んでください", options=[])
    async def select_end_day(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self.end_day = select.values[0]

    @discord.ui.select(placeholder="✨ 【回答】に使う記号のスタイルを選んでください", options=[
        discord.SelectOption(label="標準： 〇, △, × (3段階選択)", value="〇,△,×"),
        discord.SelectOption(label="詳細： ◎, 〇, △, ✕ (4段階選択)", value="◎,〇,△,✕"),
        discord.SelectOption(label="シンプル： 〇, × (可か不可か)", value="〇,×"),
    ])
    async def select_symbols(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        self.selected_symbols = select.values[0]
        await self.update_view(interaction)

    async def update_view(self, interaction: discord.Interaction):
        current_ym = f"{self.end_year}-{self.end_month}"
        for option in self.children[0].options:
            option.default = (option.value == current_ym)
        for option in self.children[1].options:
            option.default = (option.value == self.end_period)
        for option in self.children[3].options:
            option.default = (option.value == self.selected_symbols)

        days = get_valid_days(self.end_year, self.end_month, self.end_period)
        self.children[2].options = [discord.SelectOption(label=f"{i}日", value=f"{i:02d}") for i in days]
        if int(self.end_day) not in days:
            self.end_day = f"{list(days)[0]:02d}"
        for option in self.children[2].options:
            option.default = (option.value == self.end_day)

        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="📝 タイトルとコメントを入力する ➡️", style=discord.ButtonStyle.success, row=4)
    async def open_modal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        start_str = f"{self.start_year}/{self.start_month}/{self.start_day}"
        end_str = f"{self.end_year}/{self.end_month}/{self.end_day}"
        start = datetime.strptime(start_str, "%Y/%m/%d")
        end = datetime.strptime(end_str, "%Y/%m/%d")

        if end < start:
            await interaction.response.send_message("❌ エラー: 終了日は開始日より後の日付にしてください。", ephemeral=True)
            return

        date_list = []
        current = start
        while current <= end:
            date_list.append(current.strftime("%Y/%m/%d"))
            current += timedelta(days=1)

        modal = EventInfoModal(start_dt=start, end_dt=end, date_list=date_list, symbols=self.selected_symbols)
        await interaction.response.send_modal(modal)

# ==========================================
# 3. 回答 Modal
# ==========================================
class MemberResponseModal(discord.ui.Modal):
    def __init__(self, event_id, title, date_list, symbol_list, current_answers_str):
        super().__init__(title=f"【回答】{title}"[:45])
        self.event_id = event_id
        self.title = title
        self.date_list = date_list
        self.symbol_list = symbol_list

        symbol_guide = " / ".join(symbol_list)
        
        self.answers_input = discord.ui.TextInput(
            label=f"各日付の回答を入力（利用可能記号: {symbol_guide}）",
            style=discord.TextStyle.paragraph,
            placeholder="例:\n09/01: 〇\n09/02: △\n09/03: ×",
            default=current_answers_str,
            required=True,
            max_length=2000
        )
        self.add_item(self.answers_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        raw_text = self.answers_input.value

        user_answers = {}
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                parts = line.split(":", 1)
                date_part = parts[0].strip()
                symbol_part = parts[1].strip()
                
                for d in self.date_list:
                    if d.endswith(date_part) or d == date_part:
                        if symbol_part in self.symbol_list:
                            user_answers[d] = symbol_part
                        break

        for d in self.date_list:
            if d not in user_answers:
                user_answers[d] = self.symbol_list[-1]

        ans_list = [user_answers[d] for d in self.date_list]
        ans_str = ",".join(ans_list)

        conn = sqlite3.connect('scheduler.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO responses (event_id, user_id, user_name, answers)
            VALUES (?, ?, ?, ?)
        ''', (self.event_id, user_id, interaction.user.display_name, ans_str))
        conn.commit()
        conn.close()

        await interaction.followup.send(f"✅ 「{self.title}」の回答を保存しました！", ephemeral=True)

# ==========================================
# 4. イベント回答ボタンの View
# ==========================================
class EventResponseView(discord.ui.View):
    def __init__(self, event_id, title):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.title = title

    @discord.ui.button(label="📝 日程を回答・修正する", style=discord.ButtonStyle.primary, custom_id="respond_btn")
    async def respond_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        conn = sqlite3.connect('scheduler.db')
        cursor = conn.cursor()
        cursor.execute('SELECT dates, symbols FROM events WHERE id = ?', (self.event_id,))
        event = cursor.fetchone()

        if not event:
            await interaction.response.send_message("❌ エラー: イベントが見つかりません。", ephemeral=True)
            conn.close()
            return

        date_list = event[0].split(",")
        symbol_list = event[1].split(",")

        cursor.execute('SELECT answers FROM responses WHERE event_id = ? AND user_id = ?', (self.event_id, user_id))
        existing_res = cursor.fetchone()
        conn.close()

        lines = []
        if existing_res:
            saved_ans_list = existing_res[0].split(",")
            for d, a in zip(date_list, saved_ans_list):
                lines.append(f"{d[5:]}: {a}")
        else:
            for d in date_list:
                lines.append(f"{d[5:]}: {symbol_list[0]}")

        default_text = "\n".join(lines)

        modal = MemberResponseModal(
            event_id=self.event_id,
            title=self.title,
            date_list=date_list,
            symbol_list=symbol_list,
            current_answers_str=default_text
        )
        await interaction.response.send_modal(modal)

# ==========================================
# 5. ボット起動設定とデータベース初期化
# ==========================================
class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        conn = sqlite3.connect('scheduler.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                creator_id TEXT,
                dates TEXT,
                symbols TEXT,
                notification_time TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS responses (
                event_id INTEGER,
                user_id TEXT,
                user_name TEXT,
                answers TEXT,
                PRIMARY KEY (event_id, user_id)
            )
        ''')
        conn.commit()
        conn.close()
        await self.tree.sync()

bot = MyBot()

@bot.tree.command(name="create", description="日程調整イベントを立ち上げます")
async def create_event(interaction: discord.Interaction):
    await interaction.response.send_message("【ステップ1】開始する日程を選んでください：", view=StartDateSelectView(), ephemeral=True)

# ==========================================
# 6. 非同期による Web サーバー & Bot 同時起動処理
# ==========================================
async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("環境変数 DISCORD_TOKEN が設定されていません。")

    # Render の動的ポートに対応
    port = int(os.getenv("PORT", 10000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    # FastAPI と Discord Bot を同時に起動
    await asyncio.gather(
        server.serve(),
        bot.start(token)
    )

if __name__ == "__main__":
    asyncio.run(main())
