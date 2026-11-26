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
            await interaction.response.send_message("Erst nach vollständiger Zahlung kann als verkauft markiert werden.", ephemeral=True)
            return
        match = re.search(r"Property buyer: (\d+)", getattr(interaction.channel, "topic", "") or "")
        if not match:
            await interaction.response.send_message("Käufer konnte nicht ermittelt werden.", ephemeral=True)
            return
        item.update({"sold": True, "buyer_id": match.group(1), "sold_by": str(interaction.user.id)})
        _save()
        await self.cog.refresh_listing(interaction.guild, item)
        await interaction.response.send_message(f"🏡 {item['name']} wurde an <@{match.group(1)}> verkauft.")

    @discord.ui.button(label="Ticket schließen", style=discord.ButtonStyle.danger, custom_id="bloodfang:property:close")
    async def close(self, interaction, button):
        settings = _settings(self.guild_id)
        topic = getattr(interaction.channel, "topic", "") or ""
        match = re.search(r"Property buyer: (\d+)", topic)
        buyer_id = int(match.group(1)) if match else 0
        if not self.cog.is_staff(interaction, settings) and interaction.user.id != buyer_id:
            await interaction.response.send_message("Nur Käufer oder Staff können dieses Ticket schließen.", ephemeral=True)
            return
        await interaction.response.send_message("Ticket wird geschlossen …")
        await interaction.channel.delete(reason=f"Property ticket closed by {interaction.user}")


class PropertyListingView(discord.ui.View):
    def __init__(self, cog, guild_id, property_id):
        super().__init__(timeout=None)
        self.cog, self.guild_id, self.property_id = cog, int(guild_id), str(property_id)

    @discord.ui.button(label="Kaufen", style=discord.ButtonStyle.success, emoji="🛒", custom_id="bloodfang:property:buy")
    async def buy(self, interaction, button):
        await self.cog.create_purchase_ticket(interaction, self.property_id)


class PropertyCatalogView(discord.ui.View):
    def __init__(self, cog, guild_id, page, total_pages, items):
        super().__init__(timeout=None)
        self.cog, self.guild_id = cog, int(guild_id)
        self.page, self.total_pages = int(page), max(1, int(total_pages))
        for number, item in enumerate(items[self.page * 10:self.page * 10 + 10], start=1):
            pid = str(item["id"])
            button = discord.ui.Button(label=f"Kaufen {number}", style=discord.ButtonStyle.success,
                emoji="🛒", custom_id=f"bloodfang:property:catalog_buy:{self.page}:{pid}",
                row=(number - 1) // 5, disabled=bool(item.get("sold")))
            async def buy_callback(interaction, property_id=pid):
                await self.cog.create_purchase_ticket(interaction, property_id)
            button.callback = buy_callback
            self.add_item(button)
        prev = discord.ui.Button(label="Zurück", emoji="◀️", style=discord.ButtonStyle.secondary,
            custom_id=f"bloodfang:property:page:{self.page}:prev", row=4, disabled=self.page == 0)
        nxt = discord.ui.Button(label="Weiter", emoji="▶️", style=discord.ButtonStyle.primary,
            custom_id=f"bloodfang:property:page:{self.page}:next", row=4,
            disabled=self.page >= self.total_pages - 1)
        async def prev_callback(interaction):
            await self.cog.change_catalog_page(interaction, self.page - 1)
        async def next_callback(interaction):
            await self.cog.change_catalog_page(interaction, self.page + 1)
        prev.callback, nxt.callback = prev_callback, next_callback
        self.add_item(prev)
        self.add_item(nxt)





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

    async def create_purchase_ticket(self, interaction, property_id):
        settings = _settings(interaction.guild.id)
        item = next((x for x in settings.get("items", []) if x.get("id") == str(property_id)), None)
        if not item or item.get("sold"):
            await interaction.response.send_message("Dieses Anwesen ist nicht mehr verfügbar.", ephemeral=True)
            return
        if not settings.get("staff_role_id") or not settings.get("ticket_category_id"):
            await interaction.response.send_message("Das Anwesen-System ist noch nicht vollständig eingerichtet.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        category = guild.get_channel(int(settings["ticket_category_id"]))
        staff_role = guild.get_role(int(settings["staff_role_id"]))
        if not isinstance(category, discord.CategoryChannel) or staff_role is None:
            await interaction.followup.send("Ticket-Kategorie oder Staff-Rolle ist ungültig.", ephemeral=True)
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
            topic=f"Property buyer: {interaction.user.id} | Property: {property_id}",
        )
        try:
            embed = _embed(guild, item)
            embed.title = f"🏡 Kaufanfrage: {item['name']}"
            await channel.send(content=f"{interaction.user.mention} · {staff_role.mention}",
                embed=embed, view=PropertyTicketView(self, guild.id, property_id),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        except Exception:
            try:
                await channel.delete(reason="Property ticket setup failed")
            except discord.HTTPException:
                pass
            raise
        item["active_ticket_id"] = channel.id
        item["buyer_id"] = str(interaction.user.id)
        _save()
        await interaction.followup.send(f"Dein Kauf-Ticket wurde erstellt: {channel.mention}", ephemeral=True)

    def _catalog_embed(self, guild, items, page, total_pages):
        embed = discord.Embed(title="🏡 Anwesenverkauf",
            description="Wähle ein Anwesen und nutze den passenden **Kaufen**-Button. Bilder werden separat unter dieser Übersicht gesendet.",
            color=discord.Color.blurple())
        page_items = items[page * 10:(page + 1) * 10]
        if not page_items:
            embed.add_field(name="Noch keine Angebote", value="Im Dashboard kannst du Anwesen hinzufügen.", inline=False)
        for offset, item in enumerate(page_items, start=1):
            price = int(item.get("price", 0))
            paid = min(int(item.get("paid", 0)), price)
            if item.get("sold"):
                status = f"✅ Verkauft an <@{item.get('buyer_id')}>" if item.get("buyer_id") else "✅ Verkauft"
            elif paid >= price:
                status = "🟢 Vollständig bezahlt"
            else:
                status = "🟡 Verfügbar"
            embed.add_field(name=f"{page * 10 + offset}. {item.get('name', 'Anwesen')}",
                value=f"**Preis:** {price:,} €\n**Bezahlt:** {paid:,}/{price:,} €\n**Status:** {status}".replace(",", "."),
                inline=True)
        embed.set_footer(text=f"{guild.name} · Seite {page + 1}/{total_pages} · {len(items)} Anwesen")
        return embed

    async def change_catalog_page(self, interaction, page):
        items = _settings(interaction.guild.id).get("items", [])
        pages = max(1, (len(items) + 9) // 10)
        page = max(0, min(int(page), pages - 1))
        await interaction.response.edit_message(
            embed=self._catalog_embed(interaction.guild, items, page, pages),
            view=PropertyCatalogView(self, interaction.guild.id, page, pages, items))

    async def refresh_listing(self, guild, item):
        await self.publish(guild)

    async def publish(self, guild):
        settings = _settings(guild.id)
        channel = guild.get_channel(int(settings.get("listing_channel_id") or 0))
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Bitte einen gültigen Anwesen-Angebotskanal auswählen.")
        items = settings.get("items", [])
        pages = max(1, (len(items) + 9) // 10)
        ids = settings.setdefault("catalog_page_message_ids", [])
        for page in range(pages):
            embed = self._catalog_embed(guild, items, page, pages)
            view = PropertyCatalogView(self, guild.id, page, pages, items)
            msg = None
            if page < len(ids) and ids[page]:
                try:
                    msg = await channel.fetch_message(int(ids[page]))
                    await msg.edit(embed=embed, view=view)
                except discord.NotFound:
                    msg = None
            if msg is None:
                msg = await channel.send(embed=embed, view=view)
                if page < len(ids):
                    ids[page] = msg.id
                else:
                    ids.append(msg.id)
        for old_id in ids[pages:]:
            try:
                old = await channel.fetch_message(int(old_id))
                await old.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        del ids[pages:]

        # Remove legacy one-embed-per-property posts after the new catalogue is live.
        for item in items:
            legacy_id = item.pop("message_id", None)
            if legacy_id:
                try:
                    legacy = await channel.fetch_message(int(legacy_id))
                    await legacy.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        # Send each uploaded image as a normal bot message, separate from the catalogue embeds.
        for item in items:
            path = item.get("image_path")
            if not path or not Path(path).is_file():
                continue
            caption = f"🖼️ **{item.get('name', 'Anwesen')}** · {int(item.get('price', 0)):,} €".replace(",", ".")
            image_msg = None
            if item.get("image_message_id"):
                try:
                    image_msg = await channel.fetch_message(int(item["image_message_id"]))
                except discord.NotFound:
                    image_msg = None
            file = discord.File(path, filename=Path(path).name)
            if image_msg:
                await image_msg.edit(content=caption, attachments=[file], embed=None, view=None)
            else:
                image_msg = await channel.send(content=caption, file=file)
                item["image_message_id"] = image_msg.id
        _save()
        return pages

    async def cog_load(self):
        for guild_id, cfg in list(self.bot.config.items()):
            if not str(guild_id).isdigit() or not isinstance(cfg, dict):
                continue
            settings = cfg.get("property_sales", {})
            items = settings.get("items", [])
            pages = max(1, (len(items) + 9) // 10)
            for page, message_id in enumerate(settings.get("catalog_page_message_ids", [])[:pages]):
                if message_id:
                    self.bot.add_view(PropertyCatalogView(self, int(guild_id), page, pages, items), message_id=int(message_id))
            for item in items:
                if item.get("active_ticket_id"):
                    self.bot.add_view(PropertyTicketView(self, int(guild_id), item["id"]))


async def setup(bot):
    await bot.add_cog(PropertySales(bot))
