import datetime
import io
import json
import secrets
import urllib.request
from math import ceil

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from storage import data_path


def _translate(bot, guild_id, text):
    """Use IronWolf's configured per-server language for Ausweis UI and artwork."""
    translator = getattr(bot, "bot_text", None)
    if not callable(translator):
        try:
            from bot import bot_text as translator
        except Exception:
            translator = None
    if callable(translator):
        try:
            return translator(guild_id, text)
        except Exception:
            pass
    return text


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
    # Erst installierte TrueType-Schriften versuchen. Der skalierbare Pillow-Font
    # ist der wichtige Fallback: load_default() ohne size ist winzig und war
    # der Grund, warum die Schrift auf Railway trotz großer Werte kaum lesbar war.
    candidates = (
        [
            "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
        ]
        if bold else [
            "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "DejaVuSans.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _fit_text(draw, text, font, max_width):
    value = str(text).strip()
    while value and draw.textbbox((0, 0), value, font=font)[2] > max_width:
        value = value[:-2].rstrip() + "…"
    return value or "—"


def _make_id_card(guild, member, answers, questions, card_id, issue_date, translate):
    questions = questions[:5]
    width = 1200
    margin = 58
    columns = 2
    field_gap_x = 28
    field_width = (width - 2 * margin - field_gap_x) // 2
    field_height = 142
    row_gap = 22
    header_bottom = 260
    top = 300
    rows = max(1, ceil(len(questions) / columns))
    fields_bottom = top + rows * field_height + max(0, rows - 1) * row_gap
    status_y = fields_bottom + 30
    footer_y = status_y + 138
    height = footer_y + 112

    image = Image.new("RGB", (width, height), "#f7f9fc")
    draw = ImageDraw.Draw(image)

    # Ruhiger, klarer Kartenrahmen mit leichtem Innenhintergrund.
    draw.rounded_rectangle(
        (18, 18, width - 18, height - 18),
        radius=34, fill="#ffffff", outline="#cbd5e1", width=4
    )

    # Zurückhaltende Lato-Typografie statt der schweren Standard-/DejaVu-Anmutung.
    # Lato wirkt auf Ausweisen ruhiger und näher an einer professionell gesetzten Karte.
    title_font = _font(36, True)
    id_font = _font(22, True)
    status_font = _font(20, True)
    label_font = _font(23, True)
    value_font = _font(39, True)
    small_font = _font(18)

    # Serverlogo als deutlich sichtbares Wasserzeichen über die gesamte Ausweisfläche.
    # Es wird VOR Text, Feldern und Porträt eingefügt, damit alle Angaben klar lesbar bleiben.
    try:
        if guild and guild.icon:
            icon_request = urllib.request.Request(
                str(guild.icon.with_size(512).url),
                headers={"User-Agent": "IronWolf-ID/1.0"}
            )
            with urllib.request.urlopen(icon_request, timeout=8) as response:
                icon_bytes = response.read(2_000_000)
            watermark = Image.open(io.BytesIO(icon_bytes)).convert("RGBA")
            watermark = ImageOps.fit(
                watermark,
                (width - 48, height - 48),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            # 26 % Deckkraft: sichtbar über die ganze Karte, ohne Inhalte zu überdecken.
            alpha = watermark.getchannel("A").point(lambda value: int(value * 0.26))
            watermark.putalpha(alpha)
            image.paste(watermark, (24, 24), watermark)
            draw = ImageDraw.Draw(image)
    except Exception:
        # Der Ausweis wird auch dann erstellt, wenn Discord das Servericon nicht liefert.
        pass

    # Kopfbereich und alle Ausweisdaten werden nach dem Wasserzeichen gezeichnet.
    draw.text((margin, 62), translate("DIGITAL RP ID CARD"), font=title_font, fill="#172033", stroke_width=0)
    draw.text((margin, 125), f'{translate("ID CARD")}  {card_id}', font=id_font, fill="#2865e8")
    draw.ellipse((margin, 178, margin + 18, 196), fill="#20bd70")
    draw.text((margin + 32, 173), translate("ACTIVE RP ID CARD"), font=status_font, fill="#172033")

    try:
        request = urllib.request.Request(
            str(member.display_avatar.with_size(256).url),
            headers={"User-Agent": "IronWolf-ID/1.0"}
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            avatar_bytes = response.read(1_500_000)
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((126, 126), Image.Resampling.LANCZOS)
        mask = Image.new("L", (126, 126), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 125, 125), fill=255)
        image.paste(avatar, (width - margin - 126, 52), mask)
    except Exception:
        draw.ellipse((width - margin - 126, 52, width - margin, 178), fill="#e9edf4")
        initials = (member.display_name[:2] or "RP").upper()
        box = draw.textbbox((0, 0), initials, font=value_font)
        draw.text(
            (width - margin - 63 - (box[2] - box[0]) / 2,
             115 - (box[3] - box[1]) / 2),
            initials, font=value_font, fill="#343b49"
        )

    draw.line((margin, header_bottom, width - margin, header_bottom), fill="#d9e1ec", width=3)

    # Gleich große Felder in einem sauberen 2-Spalten-Raster.
    for index, question in enumerate(questions):
        col, row = index % columns, index // columns
        x = margin + col * (field_width + field_gap_x)
        y = top + row * (field_height + row_gap)
        draw.rounded_rectangle(
            (x, y, x + field_width, y + field_height),
            radius=18, fill="#f3f6fa", outline="#d5deea", width=2
        )
        label = _fit_text(draw, translate(question["label"]), label_font, field_width - 44)
        draw.text((x + 22, y + 20), label, font=label_font, fill="#526078")
        value = _fit_text(draw, answers.get(question["key"], ""), value_font, field_width - 44)
        draw.text((x + 22, y + 70), value, font=value_font, fill="#141b2a")

    # Status als eigener, gut lesbarer Bereich unter den persönlichen Angaben.
    draw.rounded_rectangle(
        (margin, status_y, width - margin, status_y + 104),
        radius=18, fill="#edf8f2", outline="#b9e5cc", width=2
    )
    draw.text((margin + 24, status_y + 15), translate("ID CARD STATUS"), font=label_font, fill="#526078")
    draw.text((margin + 24, status_y + 57), translate("ACTIVE"), font=value_font, fill="#147a46")

    draw.line((margin, footer_y, width - margin, footer_y), fill="#d9e1ec", width=2)
    draw.text((margin, footer_y + 28), f"AUSGESTELLT AM  {issue_date}", font=small_font, fill="#68758b")
    footer = "Power by IronWolf"
    footer_box = draw.textbbox((0, 0), footer, font=small_font)
    draw.text((width - margin - (footer_box[2] - footer_box[0]), footer_y + 28), footer, font=small_font, fill="#68758b")

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output

class AusweisModal(discord.ui.Modal):
    def __init__(self, bot, guild_id, member_id, channel_id):
        super().__init__(title=_translate(bot, guild_id, "Apply for a digital RP ID card"), timeout=300)
        self.bot, self.guild_id, self.member_id, self.channel_id = bot, guild_id, member_id, channel_id
        self.questions = get_questions(guild_id)
        for question in self.questions:
            question["label"] = _translate(bot, guild_id, question["label"])
            if question.get("placeholder"):
                question["placeholder"] = _translate(bot, guild_id, question["placeholder"])
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
            await interaction.response.send_message(_translate(self.bot, self.guild_id, "This form belongs to another member."), ephemeral=True)
            return
        guild = self.bot.get_guild(self.guild_id)
        member = guild.get_member(self.member_id) if guild else None
        if member is None:
            await interaction.response.send_message(_translate(self.bot, self.guild_id, "The original server or your member profile is no longer available."), ephemeral=True)
            return
        answers = {key: str(field.value).strip() for key, field in self.inputs.items()}
        for question in self.questions:
            value = answers.get(question["key"], "")
            if question["required"] and not value:
                await interaction.response.send_message(_translate(self.bot, self.guild_id, f"Please complete the **{question['label']}** field."), ephemeral=True)
                return
            if "roblox" in question["label"].casefold() and "id" in question["label"].casefold() and value and not value.isdigit():
                await interaction.response.send_message(_translate(self.bot, self.guild_id, f"**{question['label']}** must be a numeric ID."), ephemeral=True)
                return

        channel = guild.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(self.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(_translate(self.bot, self.guild_id, "The configured ID card channel could not be found."), ephemeral=True)
            return

        card_id = "BA-" + secrets.token_hex(3).upper()
        issue_date = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%d.%m.%Y")
        card_image = _make_id_card(self.bot.get_guild(self.guild_id), member, answers, self.questions, card_id, issue_date, lambda text: _translate(self.bot, self.guild_id, text))
        try:
            await channel.send(file=discord.File(io.BytesIO(card_image.getvalue()), filename="digitaler-rp-ausweis.png"), allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(_translate(self.bot, self.guild_id, "The ID card could not be published in the selected channel. Check my permissions to send messages and files."), ephemeral=True)
            return
        try:
            await interaction.user.send(_translate(self.bot, self.guild_id, "🪪 Your digital RP ID card has been created. Here is your personal copy:"), file=discord.File(io.BytesIO(card_image.getvalue()), filename="dein-digitaler-rp-ausweis.png"))
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(_translate(self.bot, self.guild_id, f"✅ Your ID card was published in {channel.mention}. I could not send you a DM."), ephemeral=True)
            return
        await interaction.response.send_message(_translate(self.bot, self.guild_id, f"✅ Your ID card was created, sent to you by DM, and published in {channel.mention}."), ephemeral=True)


class AusweisRequestButton(discord.ui.Button):
    def __init__(self, bot, guild_id, channel_id):
        super().__init__(label="Apply for ID card", emoji="🪪", style=discord.ButtonStyle.primary, custom_id=f"ironwolf:ausweis:request:{guild_id}:{channel_id}")
        self.bot, self.guild_id, self.channel_id = bot, guild_id, channel_id

    async def callback(self, interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(_translate(self.bot, self.guild_id, "Please use this button in the associated server."), ephemeral=True)
            return
        await interaction.response.send_modal(
            AusweisModal(self.bot, self.guild_id, interaction.user.id, self.channel_id)
        )


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

    @app_commands.command(name="ausweis", description="Creates an RP ID card panel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel where completed ID cards will be published")
    async def id_card(self, interaction, channel: discord.TextChannel):
        if interaction.guild is None:
            await interaction.response.send_message(_translate(self.bot, interaction.guild.id if interaction.guild else None, "This command can only be used in a server."), ephemeral=True)
            return
        panels = load_panels()
        record = {"guild_id": interaction.guild.id, "channel_id": channel.id}
        if record not in panels:
            panels.append(record)
            save_panels(panels)
        view = AusweisPanelView(self.bot, interaction.guild.id, channel.id)
        self.bot.add_view(view)
        embed = discord.Embed(
            title="🪪 Apply for a digital RP ID card",
            description="Click **Apply for ID card** and complete the form in the private pop-up. Your finished ID card will be published as an image in the selected channel and sent to you by DM.",
            color=discord.Color.from_rgb(44, 101, 232),
        )
        embed.set_footer(text="Power by IronWolf")
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Ausweis(bot))
