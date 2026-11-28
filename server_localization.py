"""Guild-aware localization for all Discord message delivery paths."""
import asyncio
from functools import wraps


def _guild_id(owner, bot):
    value = getattr(owner, "guild_id", None)
    if value:
        return int(value)
    guild = getattr(owner, "guild", None)
    if guild is not None:
        return int(guild.id)
    parent = getattr(owner, "_parent", None)
    value = getattr(parent, "guild_id", None)
    if value:
        return int(value)
    channel_id = getattr(owner, "channel_id", None)
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        if channel is not None and getattr(channel, "guild", None) is not None:
            return int(channel.guild.id)
        for candidate in getattr(bot, "guilds", ()):
            channel = candidate.get_channel(int(channel_id))
            if channel is None:
                channel = candidate.get_thread(int(channel_id))
            if channel is not None:
                return int(candidate.id)
    return None


async def _localized_embed(embed, guild_id, translate):
    if embed is None:
        return embed
    result = embed.copy()
    if result.title:
        result.title = await asyncio.to_thread(translate, guild_id, result.title)
    if result.description:
        result.description = await asyncio.to_thread(translate, guild_id, result.description)
    if result.author and result.author.name:
        result.set_author(name=await asyncio.to_thread(translate, guild_id, result.author.name),
                          url=result.author.url, icon_url=result.author.icon_url)
    if result.footer and result.footer.text:
        result.set_footer(text=await asyncio.to_thread(translate, guild_id, result.footer.text),
                          icon_url=result.footer.icon_url)
    for index, field in enumerate(result.fields):
        name = await asyncio.to_thread(translate, guild_id, field.name)
        value = await asyncio.to_thread(translate, guild_id, field.value)
        result.set_field_at(index, name=name, value=value, inline=field.inline)
    return result


async def _localize_view(view, guild_id, translate):
    """Translate visible button/select text for the destination guild.

    Restore original labels after sending because views may be reused by the
    bot across multiple guilds with different preferred locales.
    """
    if view is None:
        return []
    restore = []

    async def replace_attr(obj, attr):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            translated = await asyncio.to_thread(translate, guild_id, value)
            if translated != value:
                restore.append((obj, attr, value))
                setattr(obj, attr, translated)

    for item in getattr(view, "children", ()):
        await replace_attr(item, "label")
        await replace_attr(item, "placeholder")
        for option in getattr(item, "options", ()):
            await replace_attr(option, "label")
            await replace_attr(option, "description")
    return restore


async def _localize_kwargs(kwargs, guild_id, translate):
    content = kwargs.get("content")
    if isinstance(content, str) and content:
        kwargs["content"] = await asyncio.to_thread(translate, guild_id, content)
    embed = kwargs.get("embed")
    if embed is not None:
        kwargs["embed"] = await _localized_embed(embed, guild_id, translate)
    embeds = kwargs.get("embeds")
    if embeds is not None:
        kwargs["embeds"] = [await _localized_embed(item, guild_id, translate) for item in embeds]
    return kwargs


def install_discord_localization(bot, translate, language):
    """Translate outgoing messages, embeds, and interactive labels per guild.

    Missing/unknown guild locales resolve to English, including DMs. English
    is a no-op, so already-English content remains unchanged.
    """
    import discord

    def patch(cls, method_name):
        original = getattr(cls, method_name, None)
        if original is None or getattr(original, "_ironwolf_localized", False):
            return

        @wraps(original)
        async def localized(self, *args, **kwargs):
            guild_id = _guild_id(self, bot)
            # Always normalize outgoing text to the selected language.
            # English is the default (including DMs); a guild configured as
            # German receives German. This also converts legacy hard-coded
            # German literals back to English on English servers.
            target_guild_id = guild_id
            args = list(args)
            if args and isinstance(args[0], str):
                args[0] = await asyncio.to_thread(translate, target_guild_id, args[0])
            kwargs = await _localize_kwargs(kwargs, target_guild_id, translate)
            # Localize button/select labels as well as message and embed text.
            # Restore shared views afterward so another guild cannot inherit
            # this guild's translated labels.
            view_restore = await _localize_view(kwargs.get("view"), target_guild_id, translate)
            args = tuple(args)
            try:
                return await original(self, *args, **kwargs)
            finally:
                for item, attr, original_value in reversed(view_restore):
                    setattr(item, attr, original_value)

        localized._ironwolf_localized = True
        setattr(cls, method_name, localized)

    # Covers prefix commands, ordinary channel sends, replies, and bot-generated
    # channel messages such as welcomes, logs, alerts, and automation notices.
    patch(discord.abc.Messageable, "send")

    # Covers slash-command initial responses, edits, and interaction follow-ups.
    patch(discord.InteractionResponse, "send_message")
    patch(discord.InteractionResponse, "edit_message")
    patch(discord.Interaction, "edit_original_response")

    # Interaction follow-ups are Webhook.send calls. The interaction channel ID
    # is used to resolve the owning guild without guessing a server.
    patch(discord.Webhook, "send")
