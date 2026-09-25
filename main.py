from datetime import date, datetime, timedelta
import os
import discord
from discord import app_commands
from discord.ext import commands

# インテントの設定
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# メモリ内データ保存用 (本番環境ではデータベースやJSON保存を推奨)
events_data = {}


# ==========================================
# 5. 回答・編集用ビュー (モーダル内または選択UI)
# ==========================================
class EditResponseSelect(discord.ui.Select):

  def __init__(self, day_str: str, symbols: list[str], current_val: str):
    self.day_str = day_str
    options = [
        discord.SelectOption(
            label=sym, value=sym, default=(sym == current_val)
        )
        for sym in symbols
    ]
    super().__init__(
        placeholder=f"{day_str} の回答を選択...",
        min_values=1,
        max_values=1,
        options=options,
    )

  async def callback(self, interaction: discord.Interaction):
    # ドロップダウン選択時の処理（内部保持）
    await interaction.response.defer()


class EditResponseView(discord.ui.View):

  def __init__(
      self,
      event_id: str,
      dates: list[str],
      symbols: list[str],
      user_responses: dict,
  ):
    super().__init__(timeout=None)
    self.event_id = event_id
    self.dates = dates
    self.symbols = symbols
    self.selects = {}

    # 日付ごとにドロップダウンを配置（最大5件まで対応）
    for d in dates[:5]:
      current_val = user_responses.get(d, symbols[0])
      select = EditResponseSelect(d, symbols, current_val)
      self.selects[d] = select
      self.add_item(select)

  @discord.ui.button(
      label="入力完了", style=discord.ButtonStyle.success, row=4
  )
  async def submit(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    user_id = interaction.user.id
    event = events_data.get(self.event_id)

    if not event:
      await interaction.response.send_message(
          "イベントが見つかりませんでした。", ephemeral=True
      )
      return

    # 回答データの更新
    if user_id not in event["responses"]:
      event["responses"][user_id] = {
          "name": interaction.user.display_name,
          "answers": {},
      }

    for d, select in self.selects.items():
      if select.values:
        event["responses"][user_id]["answers"][d] = select.values[0]

    # 回答完了後、3の「回答・閲覧画面」を表示
    embed = build_summary_embed(self.event_id)
    view = EventViewView(self.event_id)
    await interaction.response.send_message(
        embed=embed, view=view, ephemeral=True
    )


# ==========================================
# 3 & 4. 回答・閲覧モーダル・ビュー
# ==========================================
def build_summary_embed(event_id: str) -> discord.Embed:
  """回答状況を表形式のテキストにしてEmbedを作成"""
  event = events_data[event_id]
  responses = event["responses"]

  embed = discord.Embed(
      title=f"📊 回答状況一覧: {event['title']}",
      color=discord.Color.blue(),
  )

  if not responses:
    embed.description = "まだ回答はありません。"
    return embed

  # 表形式（テキスト）の構築
  users = list(responses.values())
  header = "日付 | " + " | ".join([u["name"][:4] for u in users])
  lines = [header, "-" * len(header)]

  for d in event["dates"]:
    row = [d]
    for u in users:
      ans = u["answers"].get(d, "-")
      row.append(ans)
    lines.append(" | ".join(row))

  embed.description = "```text\n" + "\n".join(lines) + "\n```"
  return embed


class EventViewView(discord.ui.View):

  def __init__(self, event_id: str):
    super().__init__(timeout=None)
    self.event_id = event_id

  @discord.ui.button(
      label="回答・編集する",
      style=discord.ButtonStyle.primary,
      custom_id="btn_edit_response",
  )
  async def edit_response(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    event = events_data.get(self.event_id)
    if not event:
      await interaction.response.send_message(
          "イベントが存在しません。", ephemeral=True
      )
      return

    user_id = interaction.user.id
    user_responses = event["responses"].get(user_id, {}).get("answers", {})

    # 4. 回答・編集画面を表示
    view = EditResponseView(
        self.event_id, event["dates"], event["symbols"], user_responses
    )
    await interaction.response.send_message(
        "各候補日の回答を選択して「入力完了」を押してください：",
        view=view,
        ephemeral=True,
    )


# ==========================================
# 2. メインチャット用「回答・閲覧」ボタンビュー
# ==========================================
class MainEventView(discord.ui.View):

  def __init__(self, event_id: str):
    super().__init__(timeout=None)
    self.event_id = event_id

  @discord.ui.button(
      label="回答・閲覧",
      style=discord.ButtonStyle.success,
      custom_id="btn_open_summary",
  )
  async def open_summary(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    event = events_data.get(self.event_id)
    if not event:
      await interaction.response.send_message(
          "イベント期限切れまたはデータが存在しません。", ephemeral=True
      )
      return

    # 3. 回答状況を表示（表形式）
    embed = build_summary_embed(self.event_id)
    view = EventViewView(self.event_id)
    await interaction.response.send_message(
        embed=embed, view=view, ephemeral=True
    )


# ==========================================
# 1. /create 入力用モーダル（アクセシビリティ）
# ==========================================
class CreateEventModal(discord.ui.Modal, title="イベント作成"):

  title_input = discord.ui.TextInput(
      label="イベントのタイトル",
      placeholder="例: サークル打ち合わせ",
      required=True,
  )
  description_input = discord.ui.TextInput(
      label="イベントの説明",
      style=discord.TextStyle.paragraph,
      placeholder="例: 今月のプロジェクト進捗について",
      required=False,
  )
  start_date_input = discord.ui.TextInput(
      label="候補開始日 (YYYY-MM-DD)",
      default=date.today().strftime("%Y-%m-%d"),
      required=True,
  )
  end_date_input = discord.ui.TextInput(
      label="候補終了日 (YYYY-MM-DD)",
      default=(date.today() + timedelta(days=3)).strftime("%Y-%m-%d"),
      required=True,
  )
  symbol_type_input = discord.ui.TextInput(
      label="記号の種類 (1: 〇△✕ / 2: ◎〇△✕ / 3: 〇✕)",
      default="1",
      required=True,
  )

  async def on_submit(self, interaction: discord.Interaction):
    # 日付パース
    try:
      start_d = datetime.strptime(
          self.start_date_input.value, "%Y-%m-%d"
      ).date()
      end_d = datetime.strptime(self.end_date_input.value, "%Y-%m-%d").date()
    except ValueError:
      await interaction.response.send_message(
          "日付の形式が正しくありません (例: 2026-09-25)。", ephemeral=True
      )
      return

    # 候補日リスト生成
    dates = []
    curr = start_d
    while curr <= end_d:
      dates.append(curr.strftime("%m/%d"))
      curr += timedelta(days=1)

    # 記号の種類
    sym_map = {
        "1": ["〇", "△", "✕"],
        "2": ["◎", "〇", "△", "✕"],
        "3": ["〇", "✕"],
    }
    symbols = sym_map.get(self.symbol_type_input.value.strip(), ["〇", "△", "✕"])

    # ID発行と保存
    event_id = str(interaction.id)
    events_data[event_id] = {
        "title": self.title_input.value,
        "description": self.description_input.value or "なし",
        "start_date": self.start_date_input.value,
        "end_date": self.end_date_input.value,
        "symbols": symbols,
        "dates": dates,
        "responses": {},  # {user_id: {"name": str, "answers": {date: symbol}}}
    }

    # 2-1. 作成者へのアクセス完了通知
    await interaction.response.send_message(
        "イベント作成が完了しました！", ephemeral=True
    )

    # 2-2. Discordの公開チャットにメッセージを表示
    embed = discord.Embed(
        title=f"📅 {self.title_input.value}",
        description=self.description_input.value or "なし",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="候補期間",
        value=f"{self.start_date_input.value} 〜 {self.end_date_input.value}",
        inline=False,
    )
    embed.add_field(name="現在の回答状況", value="0人回答中", inline=False)

    view = MainEventView(event_id)
    await interaction.channel.send(embed=embed, view=view)


# ==========================================
# Slash Command 定義 (/create 引数なし)
# ==========================================
@bot.tree.command(name="create", description="イベントを作成します")
async def create_cmd(interaction: discord.Interaction):
  # 引数なしでモーダル（ポップアップ入力画面）を即座に起動
  await interaction.response.send_modal(CreateEventModal())


@bot.event
async def on_ready():
  await bot.tree.sync()
  print(f"Logged in as {bot.user} (Guild synced successfully!)")


if __name__ == "__main__":
  token = os.getenv("DISCORD_TOKEN")
  if token:
    bot.run(token)
  else:
    print("ERROR: DISCORD_TOKEN is not set.")
