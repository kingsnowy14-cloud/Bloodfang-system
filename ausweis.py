import datetime
import io
import json
import secrets
import urllib.request
from math import ceil

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from storage import data_path


PANEL_CONFIG_FILE = data_path("ausweis_panels.json")
CONFIG_FILE = data_path("config.json")
DEFAULT_QUESTIONS = [
    {"key": "field_1", "label": "RP-Name", "placeholder": "Dein RP-Name", "required": True},
    {"key": "field_2", "label": "Roblox User-ID", "placeholder": "Deine numerische Roblox-ID", "required": True},
    {"key": "field_3", "label": "Stats", "placeholder": "Deine Stats", "required": False},
    {"key": "field_4", "label": "Haustiere", "placeholder": "z. B. Ja / Nein", "required": False},
]


def load_panels():
    try:
        value = json.loads(PANEL_CONFIG_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_panels(panels):
    PANEL_CONFIG_FILE.write_text(json.dumps(panels, ensure_ascii=False, indent=2), encoding="utf-8")


def get_questions(guild_id):
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        guild_cfg = config.get(str(guild_id), {}) if isinstance(config, dict) else {}
        questions = guild_cfg.get("ausweis_questions") if isinstance(guild_cfg, dict) else None
        if isinstance(questions, list):
            valid = []
            for index, item in enumerate(questions[:5]):
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label", "")).strip()[:45]
                if not label:
                    continue
                valid.append({
                    "key": f"field_{index + 1}",
                    "label": label,
                    "placeholder": str(item.get("placeholder", "")).strip()[:100],
                    "required": bool(item.get("required", True)),
                })
            if valid:
                return valid
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return [dict(item) for item in DEFAULT_QUESTIONS]


def _font(size, bold=False):
    candidates = (
        ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit_text(draw, text, font, max_width):
    value = str(text).strip()
    while value and draw.textbbox((0, 0), value, font=font)[2] > max_width:
        value = value[:-2].rstrip() + "…"
    return value or "—"


def _make_id_card(guild, member, answers, questions, card_id, issue_date):
    questions = questions[:5]
    columns = 2
    rows = max(1, ceil(len(questions) / columns))
    top = 190
    field_height = 96
    row_gap = 12
    metadata_y = top + rows * (field_height + row_gap) + 4
    height = metadata_y + 170
    image = Image.new("RGB", (980, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    title_font, id_font = _font(38, True), _font(23, True)
    label_font, value_font = _font(23, True), _font(37, True)
    small_font, status_font = _font(19), _font(21, True)

    draw.rounded_rectangle((18, 18, 962, height - 18), radius=25, fill="#ffffff", outline="#cbd3df", width=3)
    # Das Serverlogo liegt nur als sehr dezentes Wasserzeichen unter den Angaben.
    try:
        if guild and guild.icon:
            icon_request = urllib.request.Request(str(guild.icon.with_size(512).url), headers={"User-Agent": "Bloodfang-RP-ID/1.0"})
            with urllib.request.urlopen(icon_request, timeout=4) as response:
                icon_bytes = response.read(1_500_000)
            watermark = Image.open(io.BytesIO(icon_bytes)).convert("RGBA")
            watermark.thumbnail((410, 410), Image.Resampling.LANCZOS)
            watermark.putalpha(watermark.getchannel("A").point(lambda value: int(value * 0.09)))
            image.paste(watermark, (500, 220), watermark)
    except Exception:
        pass
    draw.text((46, 40), "DIGITALER RP-AUSWEIS", font=title_font, fill="#171b23")
    draw.text((558, 45), f"AUSWEIS-ID {card_id}", font=id_font, fill="#2865e8")
    draw.ellipse((597, 81, 610, 94), fill="#20c46b")
    draw.text((619, 78), "AKTIVER RP-AUSWEIS", font=status_font, fill="#171b23")

    try:
        request = urllib.request.Request(str(member.display_avatar.with_size(128).url), headers={"User-Agent": "Bloodfang-RP-ID/1.0"})
        with urllib.request.urlopen(request, timeout=4) as response:
            avatar_bytes = response.read(1_500_000)
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((96, 96), Image.Resampling.LANCZOS)
        mask = Image.new("L", (96, 96), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 95, 95), fill=255)
        image.paste(avatar, (830, 47), mask)
    except Exception:
        draw.ellipse((830, 47, 926, 143), fill="#e9edf4")
        initials = (member.display_name[:2] or "RP").upper()
        box = draw.textbbox((0, 0), initials, font=value_font)
        draw.text((878 - (box[2] - box[0]) / 2, 95 - (box[3] - box[1]) / 2), initials, font=value_font, fill="#343b49")

    draw.line((46, 168, 935, 168), fill="#d9dfe8", width=2)
    for index, question in enumerate(questions):
        col, row = index % columns, index // columns
        x, y = 46 + col * 456, top + row * (field_height + row_gap)
        draw.rounded_rectangle((x, y, x + 433, y + field_height), radius=12, fill="#f1f3f6", outline="#d5dce6", width=1)
        draw.text((x + 16, y + 12), _fit_text(draw, question["label"], label_font, 398), font=label_font, fill="#697386")
        value = answers.get(question["key"], "")
        draw.text((x + 16, y + 48), _fit_text(draw, value, value_font, 398), font=value_font, fill="#191d25")

    draw.rounded_rectangle((46, metadata_y, 935, metadata_y + 68), radius=12, fill="#f1f3f6", outline="#d5dce6", width=1)
    draw.text((62, metadata_y + 10), "AUSWEISSTATUS", font=label_font, fill="#697386")
    draw.text((62, metadata_y + 33), "Aktiv", font=value_font, fill="#191d25")
    draw.line((46, metadata_y + 88, 935, metadata_y + 88), fill="#d9dfe8", width=2)
    draw.text((46, metadata_y + 108), f"Ausgestellt am: {issue_date}", font=small_font, fill="#778195")
    draw.text((690, metadata_y + 108), "DIGITALES RP-AUSWEISSYSTEM", font=small_font, fill="#778195")

    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


class AusweisModal(discord.ui.Modal):
    def __init__(self, bot, guild_id, member_id, channel_id):
        super().__init__(title="Digitalen RP-Ausweis beantragen", timeout=300)
        self.bot, self.guild_id, self.member_id, self.channel_id = bot, guild_id, member_id, channel_id
        self.questions = get_questions(guild_id)
        self.inputs = {}
        for question in self.questions:
            field = discord.ui.TextInput(
                label=question["label"],
                placeholder=question["placeholder"] or None,
                required=question["required"],
                min_length=1 if question["required"] else 0,
                max_length=200,
                style=discord.TextStyle.short,
            )
            self.inputs[question["key"]] = field
            self.add_item(field)

    async def on_submit(self, interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("Dieses Formular gehört zu einem anderen Mitglied.", ephemeral=True)
            return
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(self.member_id) if guild else None
        if member is None:
            await interaction.response.send_message("Der ursprüngliche Server oder dein Mitgliedsprofil ist nicht verfügbar.", ephemeral=True)
            return
        answers = {key: str(field.value).strip() for key, field in self.inputs.items()}
        for question in self.questions:
            value = answers.get(question["key"], "")
            if question["required"] and not value:
                await interaction.response.send_message(f"Bitte fülle das Feld **{question['label']}** aus.", ephemeral=True)
                return
            if "roblox" in question["label"].casefold() and "id" in question["label"].casefold() and value and not value.isdigit():
                await interaction.response.send_message(f"**{question['label']}** muss eine numerische ID sein.", ephemeral=True)
                return

        channel = guild.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Der konfigurierte Ausweiskanal wurde nicht gefunden.", ephemeral=True)
            return

        card_id = "BA-" + secrets.token_hex(3).upper()
        issue_date = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%d.%m.%Y")
        card_image = _make_id_card(self.bot.get_guild(self.guild_id), member, answers, self.questions, card_id, issue_date)
        try:
            await channel.send(file=discord.File(io.BytesIO(card_image.getvalue()), filename="digitaler-rp-ausweis.png"), allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Der Ausweis konnte im ausgewählten Kanal nicht veröffentlicht werden. Prüfe meine Rechte für Nachrichten und Dateien.", ephemeral=True)
            return
        try:
            await interaction.user.send("🪪 Dein digitaler RP-Ausweis wurde erstellt. Hier ist deine persönliche Kopie:", file=discord.File(io.BytesIO(card_image.getvalue()), filename="dein-digitaler-rp-ausweis.png"))
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(f"✅ Dein Ausweis wurde in {channel.mention} veröffentlicht. Ich konnte dir keine DM senden.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Dein Ausweis wurde erstellt, dir per DM geschickt und in {channel.mention} veröffentlicht.", ephemeral=True)


class OpenAusweisFormView(discord.ui.View):
    def __init__(self, bot, guild_id, member_id, channel_id):
        super().__init__(timeout=300)
        self.bot, self.guild_id, self.member_id, self.channel_id = bot, guild_id, member_id, channel_id

    @discord.ui.button(label="Formular öffnen", emoji="📝", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction, button):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("Dieser Formular-Button gehört zu einer anderen Person.", ephemeral=True)
            return
        await interaction.response.send_modal(AusweisModal(self.bot, self.guild_id, interaction.user.id, self.channel_id))


class AusweisRequestButton(discord.ui.Button):
    def __init__(self, bot, guild_id, channel_id):
        super().__init__(label="Ausweis beantragen", emoji="🪪", style=discord.ButtonStyle.primary, custom_id=f"ironwolf:ausweis:request:{guild_id}:{channel_id}")
        self.bot, self.guild_id, self.channel_id = bot, guild_id, channel_id

    async def callback(self, interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Bitte nutze diesen Button auf dem zugehörigen Server.", ephemeral=True)
            return
        try:
            await interaction.user.send(
                f"Hallo! Du beantragst einen digitalen RP-Ausweis für **{interaction.guild.name}**. Öffne das Formular über den Button.",
                view=OpenAusweisFormView(self.bot, self.guild_id, interaction.user.id, self.channel_id),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Ich kann dir keine DM senden. Aktiviere bitte Direktnachrichten für diesen Server.", ephemeral=True)
            return
        await interaction.response.send_message("📩 Ich habe dir das private Ausweisformular per DM geschickt.", ephemeral=True)


class AusweisPanelView(discord.ui.View):
    def __init__(self, bot, guild_id, output_channel_id):
        super().__init__(timeout=None)
        self.add_item(AusweisRequestButton(bot, guild_id, output_channel_id))


class Ausweis(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        for panel in load_panels():
            try:
                self.bot.add_view(AusweisPanelView(self.bot, int(panel["guild_id"]), int(panel.get("output_channel_id", panel["channel_id"]))))
            except (KeyError, TypeError, ValueError):
                continue

    @app_commands.command(name="ausweis", description="Erstellt ein RP-Ausweis-Panel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Kanal, in dem fertige Ausweise veröffentlicht werden")
    async def id_card(self, interaction, channel: discord.TextChannel):
        if interaction.guild is None:
            await interaction.response.send_message("Dieser Befehl kann nur auf einem Server verwendet werden.", ephemeral=True)
            return
        panels = load_panels()
        record = {"guild_id": interaction.guild.id, "channel_id": channel.id}
        if record not in panels:
            panels.append(record)
            save_panels(panels)
        view = AusweisPanelView(self.bot, interaction.guild.id, channel.id)
        self.bot.add_view(view)
        embed = discord.Embed(
            title="🪪 Digitalen RP-Ausweis beantragen",
            description="Klicke auf **Ausweis beantragen**. Das Formular wird dir privat per DM geschickt. Dein fertiger Ausweis wird als Bild im ausgewählten Kanal und zusätzlich per DM veröffentlicht.",
            color=discord.Color.from_rgb(44, 101, 232),
        )
        embed.set_footer(text="Bloodfang RP · Digitales Ausweissystem")
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Ausweis(bot))
