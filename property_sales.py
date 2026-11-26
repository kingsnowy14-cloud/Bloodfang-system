import asyncio
import io
import json
import re
from pathlib import Path

import discord
from discord.ext import commands
from storage import data_path

IMAGE_DIR = data_path("property_images")
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def _save():
    bot = PROPERTY_BOT
    bot.save_json(bot.CONFIG_FILE, bot.config)


PROPERTY_BOT = None


def _settings(guild_id):
    return PROPERTY_BOT.config.setdefault(str(guild_id), {}).setdefault("property_sales", {})


def _embed(guild, item):
    price = int(item.get("price", 0))
    paid = min(int(item.get("paid", 0)), price)
    sold = bool(item.get("sold"))
    buyer = item.get("buyer_id")
    status = f"✅ Verkauft an <@{buyer}>" if sold and buyer else ("🟢 Vollständig bezahlt – Verkauf durch Staff möglich" if paid >= price else "🟡 Verfügbar")
    embed = discord.Embed(
        title=f"🏡 {item.get('name', 'Anwesen')}",
        description=(
            f"**Kaufpreis:** {price:,} €\n"
            f"**Bezahlt:** {paid:,}/{price:,} €\n"
            f"**Status:** {status}"
        ).replace(",", "."),
        color=discord.Color.green() if sold else discord.Color.blurple(),
    )
    embed.set_footer(text=f"{guild.name} · Anwesenverkauf")
    if item.get("image_url"):
        embed.set_image(url=item["image_url"])
    return embed


class PaymentModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, property_id):
        super().__init__(title="Zahlungsstand setzen")
        self.cog, self.guild_id, self.property_id = cog, int(guild_id), str(property_id)
        self.amount = discord.ui.TextInput(label="Bereits bezahlt (Euro)", placeholder="170", max_length=12)
        self.add_item(self.amount)

    async def on_submit(self, interaction):
        settings = _settings(self.guild_id)
        item = next((x for x in settings.get("items", []) if x.get("id") == self.property_id), None)
        if not item or not self.cog.is_staff(interaction, settings):
            await interaction.response.send_message("Keine Berechtigung oder Anwesen nicht gefunden.", ephemeral=True)
            return
        try:
            paid = int(str(self.amount.value).strip().replace(".", "").replace(",", ""))
            if paid < 0 or paid > int(item["price"]):
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Bitte einen gültigen Betrag bis zum Kaufpreis eingeben.", ephemeral=True)
            return
        item["paid"] = paid
        _save()
        await self.cog.refresh_listing(interaction.guild, item)
        await interaction.response.send_message(f"Zahlungsstand aktualisiert: {paid}/{item['price']} €.", ephemeral=True)


class PropertyTicketView(discord.ui.View):
    def __init__(self, cog, guild_id, property_id):
        super().__init__(timeout=None)
        self.cog, self.guild_id, self.property_id = cog, int(guild_id), str(property_id)

    @discord.ui.button(label="Ticket schließen", style=discord.ButtonStyle.danger, custom_id="bloodfang:property:close")
    async def close(self, interaction, button):
        settings = _settings(self.guild_id)
        if not self.cog.is_staff(interaction, settings) and interaction.user.id != getattr(interaction.channel, "topic_owner_id", None):
            await interaction.response.send_message("Nur Staff kann dieses Ticket schließen.", ephemeral=True)
            return
        await interaction.response.send_message("Ticket wird geschlossen …")
        await interaction.channel.delete(reason=f"Property ticket closed by {interaction.user}")


class PropertyListingView(discord.ui.View):
    def __init__(self, cog, guild_id, property_id):
        super().__init__(timeout=None)
        self.cog, self.guild_id, self.property_id = cog, int(guild_id), str(property_id)

    @discord.ui.button(label="Kaufen", style=discord.ButtonStyle.success, emoji="🛒", custom_id="bloodfang:property:buy")
    async def buy(self, interaction, button):
        settings = _settings(self.guild_id)
        item = next((x for x in settings.get("items", []) if x.get("id") == self.property_id), None)
        if not item or item.get("sold"):
            await interaction.response.send_message("Dieses Anwesen ist nicht mehr verfügbar.", ephemeral=True)
            return
        if not settings.get("staff_role_id") or not settings.get("ticket_category_id"):
            await interaction.response.send_message("Das Anwesen-System ist noch nicht vollständig eingerichtet.", ephemeral=True)
            return
        guild = interaction.guild
        category = guild.get_channel(int(settings["ticket_category_id"]))
        staff_role = guild.get_role(int(settings["staff_role_id"]))
        if not isinstance(category, discord.CategoryChannel) or staff_role is None:
            await interaction.response.send_message("Ticket-Kategorie oder Staff-Rolle ist ungültig.", ephemeral=True)
            return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        channel = await guild.create_text_channel(
            name=f"anwesen-{re.sub(r'[^a-z0-9-]', '', interaction.user.name.lower())[:18]}",
            category=category, overwrites=overwrites,
            topic=f"Property buyer: {interaction.user.id} | Property: {self.property_id}",
        )
        embed = _embed(guild, item)
        embed.title = f"🏡 Kaufanfrage: {item['name']}"
        await channel.send(
            content=f"{interaction.user.mention} · {staff_role.mention}",
            embed=embed, view=PropertyTicketView(self.cog, guild.id, self.property_id),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        item["active_ticket_id"] = channel.id
        _save()
        await interaction.response.send_message(f"Dein Kauf-Ticket wurde erstellt: {channel.mention}", ephemeral=True)


class PropertyStaffView(discord.ui.View):
    def __init__(self, cog, guild_id, property_id):
        super().__init__(timeout=None)
        self.cog, self.guild_id, self.property_id = cog, int(guild_id), str(property_id)

    @discord.ui.button(label="Geld setzen", style=discord.ButtonStyle.primary, emoji="💶", custom_id="bloodfang:property:payment")
    async def payment(self, interaction, button):
        settings = _settings(self.guild_id)
        if not self.cog.is_staff(interaction, settings):
            await interaction.response.send_message("Nur Staff darf den Zahlungsstand ändern.", ephemeral=True)
            return
        await interaction.response.send_modal(PaymentModal(self.cog, self.guild_id, self.property_id))

    @discord.ui.button(label="Als verkauft markieren", style=discord.ButtonStyle.success, emoji="🏷️", custom_id="bloodfang:property:sold")
    async def sold(self, interaction, button):
        settings = _settings(self.guild_id)
        item = next((x for x in settings.get("items", []) if x.get("id") == self.property_id), None)
        if not self.cog.is_staff(interaction, settings):
            await interaction.response.send_message("Nur Staff darf Anwesen verkaufen.", ephemeral=True)
            return
        if not item or int(item.get("paid", 0)) < int(item.get("price", 0)):
            await interaction.response.send_message("Das Anwesen kann erst nach vollständiger Zahlung als verkauft markiert werden.", ephemeral=True)
            return
        buyer_id = item.get("buyer_id")
        if not buyer_id:
            channel = interaction.guild.get_channel(int(item.get("active_ticket_id") or 0))
            topic = getattr(channel, "topic", "") or ""
            match = re.search(r"Property buyer: (\d+)", topic)
            buyer_id = match.group(1) if match else None
        if not buyer_id:
            await interaction.response.send_message("Käufer konnte nicht ermittelt werden. Bitte Ticket prüfen.", ephemeral=True)
            return
        item.update({"sold": True, "buyer_id": str(buyer_id), "sold_by": str(interaction.user.id)})
        _save()
        await self.cog.refresh_listing(interaction.guild, item)
        await interaction.response.send_message(f"🏡 {item['name']} wurde als verkauft markiert an <@{buyer_id}>.", ephemeral=False)


class PropertySales(commands.Cog):
    def __init__(self, bot):
        global PROPERTY_BOT
        PROPERTY_BOT = bot
        self.bot = bot

    def is_staff(self, interaction, settings):
        role_id = settings.get("staff_role_id")
        role = interaction.guild.get_role(int(role_id)) if str(role_id or "").isdigit() else None
        return bool(interaction.user.guild_permissions.administrator or (role and role in getattr(interaction.user, "roles", [])))

    async def refresh_listing(self, guild, item):
        settings = _settings(guild.id)
        channel = guild.get_channel(int(settings.get("listing_channel_id") or 0))
        message_id = item.get("message_id")
        if channel and message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(embed=_embed(guild, item), view=PropertyListingView(self, guild.id, item["id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        if item.get("sold"):
            sold_channel = guild.get_channel(int(settings.get("sold_channel_id") or 0))
            if sold_channel:
                sold_message_id = item.get("sold_message_id")
                embed = _embed(guild, item)
                embed.title = f"🏡 Verkauft: {item['name']}"
                if sold_message_id:
                    try:
                        msg = await sold_channel.fetch_message(int(sold_message_id))
                        await msg.edit(embed=embed, view=None)
                        return
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass
                msg = await sold_channel.send(embed=embed)
                item["sold_message_id"] = msg.id
                _save()

    async def publish(self, guild):
        settings = _settings(guild.id)
        channel = guild.get_channel(int(settings.get("listing_channel_id") or 0))
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Bitte einen gültigen Anwesen-Angebotskanal auswählen.")
        published = 0
        for item in settings.get("items", []):
            if item.get("message_id"):
                try:
                    message = await channel.fetch_message(int(item["message_id"]))
                    await message.edit(embed=_embed(guild, item), view=PropertyListingView(self, guild.id, item["id"]))
                    published += 1
                    continue
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            file = None
            if item.get("image_path"):
                try:
                    file = discord.File(item["image_path"], filename=Path(item["image_path"]).name)
                except OSError:
                    file = None
            embed = _embed(guild, item)
            if file:
                embed.set_image(url=f"attachment://{file.filename}")
            msg = await channel.send(embed=embed, view=PropertyListingView(self, guild.id, item["id"]), file=file)
            if file:
                item["image_url"] = msg.attachments[0].url
                embed.set_image(url=item["image_url"])
                await msg.edit(embed=embed)
            item["message_id"] = msg.id
            published += 1
        _save()
        return published

    async def cog_load(self):
        for guild_id, cfg in list(self.bot.config.items()):
            if not str(guild_id).isdigit() or not isinstance(cfg, dict):
                continue
            settings = cfg.get("property_sales", {})
            for item in settings.get("items", []):
                if item.get("message_id"):
                    self.bot.add_view(PropertyListingView(self, int(guild_id), item["id"]), message_id=int(item["message_id"]))
                if item.get("active_ticket_id"):
                    self.bot.add_view(PropertyTicketView(self, int(guild_id), item["id"]))
                if item.get("message_id"):
                    self.bot.add_view(PropertyStaffView(self, int(guild_id), item["id"]))


async def setup(bot):
    await bot.add_cog(PropertySales(bot))
