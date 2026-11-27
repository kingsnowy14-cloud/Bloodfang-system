import datetime
import io
import json
import secrets
import urllib.request
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from storage import data_path


PANEL_CONFIG_FILE = data_path("ausweis_panels.json")


def load_panels() -> list[dict]:
    try:
        with PANEL_CONFIG_FILE.open("r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_panels(panels: list[dict]) -> None:
    with PANEL_CONFIG_FILE.open("w", encoding="utf-8") as file:
        json.dump(panels, file, ensure_ascii=False, indent=2)


def _font(size: int, bold: bool = False):
    candidates = (
        ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text: str, font, max_width: int) -> str:
    value = str(text).strip()
    while value and draw.textbbox((0, 0), value, font=font)[2] > max_width:
        value = value[:-2].rstrip() + "…"
    return value or "—"


def _make_id_card(member: discord.Member, rp_name: str, rp_age: int,
                  roblox_name: str, roblox_user_id: str, card_id: str,
                  issue_date: str) -> io.BytesIO:
    """Render the RP ID card using only submitted identity details and system metadata."""
    image = Image.new("RGB", (980, 610), "#ffffff")
    draw = ImageDraw.Draw(image)

    title_font = _font(31, True)
    id_font = _font(19, True)
    label_font = _font(13, True)
    value_font = _font(22, True)
    small_font = _font(15)
    status_font = _font(16, True)

    # Main card and header.
    draw.rounded_rectangle((18, 18, 962, 592), radius=25, fill="#ffffff", outline="#cbd3df", width=3)
    draw.text((46, 40), "DIGITALER BÜRGERAUSWEIS", font=title_font, fill="#171b23)
    draw.text((558, 45), f"AUSWEIS-ID {card_id}", font=id_font, fill="#2865e8")
    draw.ellipse((597, 81, 610, 94), fill="#20c46b")
    draw.text((619, 78), "AKTIVER RP-AUSWEIS", font=status_font, fill="#171b23")

    # Member portrait (Discord avatar; no fabricated Roblox portrait).
    avatar_box = (821, 38, 935, 152)
    draw.ellipse(avatar_box, fill="#ffffff", outline="#cbd3df", width=3)
    try:
        request = urllib.request.Request(
            str(member.display_avatar.with_size(128).url),
            headers={"User-Agent": "Bloodfang-RP-ID/1.0"},
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            avatar_bytes = response.read(1_500_000)
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((96, 96), Image.Resampling.LANCZOS)
        mask = Image.new("L", (96, 96), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 95, 95), fill=255)
        image.paste(avatar, (830, 47), mask)
    except Exception:
        initials = (member.display_name[:2] or "RP").upper()
        draw.ellipse((830, 47, 926, 143), fill="#e9edf4")
        box = draw.textbbox((0, 0), initials, font=value_font)
        draw.text((878 - (box[2] - box[0]) / 2, 95 - (box[3] - box[1]) / 2), initials, font=value_font, fill="#343b49")

    draw.line((46, 168, 935, 168), fill="#d9dfe8", width=2)

    # Two-column field layout matching the supplied reference.
    fields = [
        ("RP-NAME", rp_name),
        ("ROBLOX-BENUTZERNAME", roblox_name),
        ("RP-ALTER", str(rp_age)),
        ("ROBLOX USER-ID", roblox_user_id),
        ("AUSWEIS-ID", card_id),
        ("AUSSTELLUNGSDATUM", issue_date),
    ]
    positions = [
        (46, 190), (502, 190),
        (46, 272), (502, 272),
        (46, 354), (502, 354),
    ]
    for (label, value), (x, y) in zip(fields, positions):
        draw.rounded_rectangle((x, y, x + 433, y + 69), radius=12, fill="#f1f3f6", outline="#d5dce6", width=1)
        draw.text((x + 16, y + 11), label, font=label_font, fill="#697386")
        draw.text((x + 16, y + 34), _fit_text(draw, value, value_font, 398), font=value_font, fill="#191d25")

    draw.rounded_rectangle((46, 436, 935, 505), radius=12, fill="#f1f3f6", outline="#d5dce6", width=1)
    draw.text((62, 447), "AUSWEISSTATUS", font=label_font, fill="#697386")
    draw.text((62, 470), "Aktiv", font=value_font, fill="#191d25")

    draw.line((46, 525, 935, 525), fill="#d9dfe8", width=2)
    draw.text((46, 545), f"Ausgestellt am: {issue_date}", font=small_font, fill="#778195")
    draw.text((690, 545), "DIGITALES RP-AUSWEISSYSTEM", font=small_font, fill="#778195")

    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


class AusweisModal(discord.ui.Modal, title="Digitalen RP-Ausweis beantragen"):
    rp_name = discord.ui.TextInput(
        label="RP-Name", placeholder="Vorname Nachname", min_length=2, max_length=40
    )
    roblox_name = discord.ui.TextInput(
        label="Roblox-Benutzername", placeholder="Dein exakter Roblox-Name", min_length=3, max_length=20
    )
    rp_age = discord.ui.TextInput(
        label="RP-Alter", placeholder="z. B. 34", min_length=1, max_length=3
    )
    roblox_user_id = discord.ui.TextInput(
        label="Roblox User-ID", placeholder="Nur die numerische Roblox-ID", min_length=1, max_length=20
    )

    def __init__(self, bot: commands.Bot, guild_id: int, member_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.member_id = member_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("Dieses Formular gehört zu einem anderen Mitglied.", ephemeral=True)
            return

        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            await interaction.response.send_message("Der ursprüngliche Server ist nicht verfügbar.", ephemeral=True)
            return
        member = guild.get_member(self.member_id)
        if member is None:
            await interaction.response.send_message("Du bist nicht mehr Mitglied dieses Servers.", ephemeral=True)
            return

        rp_name_value = str(self.rp_name.value).strip()
        roblox_name_value = str(self.roblox_name.value).strip()
        user_id_value = str(self.roblox_user_id.value).strip()
        try:
            age_value = int(str(self.rp_age.value).strip())
            if not 1 <= age_value <= 120:
                raise ValueError
            if not user_id_value.isdigit():
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Bitte gib ein RP-Alter zwischen 1 und 120 und eine rein numerische Roblox User-ID ein.",
                ephemeral=True,
            )
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

        # The ID and issue date are generated by the system; all personal fields
        # come directly from the member's submitted form.
        card_id = "BA-" + secrets.token_hex(3).upper()
        issue_date = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%d.%m.%Y")
        card_image = _make_id_card(
            member, rp_name_value, age_value, roblox_name_value,
            user_id_value, card_id, issue_date,
        )

        try:
            channel_file = discord.File(io.BytesIO(card_image.getvalue()), filename="digitaler-buergerausweis.png")
            await channel.send(file=channel_file, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Der Ausweis konnte nicht im ausgewählten Kanal gesendet werden. Prüfe meine Rechte für Nachrichten und Dateien.",
                ephemeral=True,
            )
            return

        try:
            dm_file = discord.File(io.BytesIO(card_image.getvalue()), filename="dein-digitaler-buergerausweis.png")
            await interaction.user.send(
                "🪪 Dein digitaler RP-Ausweis wurde erstellt. Hier ist deine persönliche Kopie:",
                file=dm_file,
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                f"✅ Dein Ausweis wurde in {channel.mention} veröffentlicht. Ich konnte dir keine DM senden – aktiviere dafür deine Direktnachrichten.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Dein digitaler RP-Ausweis wurde erstellt, dir per DM geschickt und in {channel.mention} veröffentlicht.",
            ephemeral=True,
        )


class OpenAusweisFormView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, member_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.member_id = member_id
        self.channel_id = channel_id

    @discord.ui.button(label="Formular öffnen", emoji="📝", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("Dieser Formular-Button gehört zu einer anderen Person.", ephemeral=True)
            return
        await interaction.response.send_modal(
            AusweisModal(self.bot, self.guild_id, interaction.user.id, self.channel_id)
        )


class AusweisRequestButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, guild_id: int, channel_id: int):
        super().__init__(
            label="Ausweis beantragen",
            emoji="🪪",
            style=discord.ButtonStyle.primary,
            custom_id=f"ironwolf:ausweis:request:{guild_id}:{channel_id}",
        )
        self.bot = bot
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Bitte nutze diesen Button auf dem zugehörigen Server.", ephemeral=True)
            return
        try:
            dm = await interaction.user.create_dm()
            await dm.send(
                f"Hallo! Du beantragst einen digitalen RP-Ausweis für **{interaction.guild.name}**. Fülle das Formular über den Button privat aus.",
                view=OpenAusweisFormView(self.bot, self.guild_id, interaction.user.id, self.channel_id),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Ich kann dir keine DM senden. Aktiviere bitte Direktnachrichten für diesen Server und drücke den Button erneut.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("📩 Ich habe dir eine DM mit dem privaten Ausweisformular geschickt.", ephemeral=True)


class AusweisPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(AusweisRequestButton(bot, guild_id, channel_id))


class Ausweis(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for panel in load_panels():
            try:
                guild_id = int(panel["guild_id"])
                channel_id = int(panel["channel_id"])
                self.bot.add_view(AusweisPanelView(self.bot, guild_id, channel_id))
            except (KeyError, TypeError, ValueError):
                continue

    @app_commands.command(
        name="ausweis",
        description="Erstellt ein Ausweis-Panel und legt den Ausgabekanal fest",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Kanal, in dem fertige Ausweise veröffentlicht werden")
    async def id_card(self, interaction: discord.Interaction, channel: discord.TextChannel):
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
            description=(
                "Du möchtest einen digitalen RP-Ausweis? Klicke auf **Ausweis beantragen**. "
                "Der Bot sendet dir das Formular privat per DM. Nach dem Ausfüllen erhältst du "
                "deinen Ausweis ebenfalls per DM; im ausgewählten Kanal wird ausschließlich das Ausweisbild veröffentlicht."
            ),
            color=discord.Color.from_rgb(44, 101, 232),
        )
        embed.set_footer(text="Bloodfang RP · Digitales Ausweissystem")
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Ausweis(bot))
