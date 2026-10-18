import os
import io
import re
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
STAFF_ROLE_IDS = {
    int(x.strip()) for x in os.getenv("STAFF_ROLE_IDS", "").split(",")
    if x.strip().isdigit()
}
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0") or 0)
APPLICATION_REVIEW_CHANNEL_ID = int(os.getenv("APPLICATION_REVIEW_CHANNEL_ID", "0") or 0)
TRANSCRIPT_LOG_CHANNEL_ID = int(os.getenv("TRANSCRIPT_LOG_CHANNEL_ID", "0") or 0)
PANEL_TITLE = os.getenv("PANEL_TITLE", "Dravixplayz Bot | pravite:1")
BRAND_COLOR = discord.Color.from_rgb(151, 92, 255)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
ticket_counter = 0


def is_staff(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or any(
        role.id in STAFF_ROLE_IDS for role in member.roles
    )


def staff_overwrites(guild: discord.Guild):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, attach_files=True
        ),
    }
    for role_id in STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True
            )
    return overwrites


def ticket_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=BRAND_COLOR)
    embed.set_footer(text=PANEL_TITLE)
    return embed


async def find_existing_ticket(guild: discord.Guild, user_id: int):
    for channel in guild.text_channels:
        topic = channel.topic or ""
        if f"ticket_owner={user_id}" in topic and "ticket_closed=true" not in topic:
            return channel
    return None


async def create_ticket(
    interaction: discord.Interaction,
    category: str,
    answers: dict[str, str],
    application_position: str | None = None,
):
    global ticket_counter
    guild = interaction.guild
    user = interaction.user
    if guild is None or not isinstance(user, discord.Member):
        await interaction.followup.send("Please use this inside the server.", ephemeral=True)
        return

    existing = await find_existing_ticket(guild, user.id)
    if existing:
        await interaction.followup.send(
            f"You already have an open ticket: {existing.mention}", ephemeral=True
        )
        return

    ticket_counter += 1
    safe_name = re.sub(r"[^a-z0-9-]+", "-", user.display_name.lower()).strip("-")[:18] or "user"
    channel_name = f"ticket-{ticket_counter:04d}-{safe_name}"
    category_obj = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
    if category_obj and not isinstance(category_obj, discord.CategoryChannel):
        category_obj = None

    overwrites = staff_overwrites(guild)
    overwrites[user] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        attach_files=True, embed_links=True
    )

    channel = await guild.create_text_channel(
        channel_name,
        category=category_obj,
        overwrites=overwrites,
        topic=f"ticket_owner={user.id}; ticket_type={category}; ticket_closed=false",
        reason=f"{category} ticket opened by {user}",
    )

    lines = [f"**{key}:** {value}" for key, value in answers.items() if value.strip()]
    if application_position:
        lines.insert(0, f"**Requested position:** {application_position}")
    embed = ticket_embed(
        f"🎫 {category} — {channel.name}",
        f"Welcome {user.mention}! Your private ticket is open.\n\n"
        + ("\n".join(lines) if lines else "Please describe how we can help.")
        + "\n\nA staff member will respond when available."
    )
    embed.add_field(name="Ticket ID", value=f"DP-{ticket_counter:04d}", inline=True)
    embed.add_field(name="Status", value="Waiting for staff", inline=True)
    await channel.send(content=f"{user.mention} " + " ".join(
        guild.get_role(rid).mention for rid in STAFF_ROLE_IDS if guild.get_role(rid)
    ), embed=embed, view=TicketControls())

    await interaction.followup.send(
        f"Your private ticket has been created: {channel.mention}", ephemeral=True
    )

    if category == "Staff Application" and APPLICATION_REVIEW_CHANNEL_ID:
        review_channel = guild.get_channel(APPLICATION_REVIEW_CHANNEL_ID)
        if isinstance(review_channel, discord.TextChannel):
            review_embed = ticket_embed("🧑‍💻 New Staff Application", f"Applicant: {user.mention}\nTicket: {channel.mention}")
            review_embed.add_field(name="Requested position", value=application_position or "Not specified", inline=False)
            for key, value in answers.items():
                if value.strip():
                    review_embed.add_field(name=key, value=value[:1024], inline=False)
            review_embed.set_footer(text=f"Applicant ID: {user.id}")
            await review_channel.send(embed=review_embed, view=ApplicationReview(user.id, channel.id, application_position or "Staff"))


class TicketModal(discord.ui.Modal):
    def __init__(self, category: str):
        super().__init__(title=f"{category} — New Ticket", timeout=300)
        self.category = category
        self.username = discord.ui.TextInput(
            label="Minecraft / Discord username", placeholder="Your username",
            max_length=100, required=True
        )
        self.description = discord.ui.TextInput(
            label="Describe your issue", placeholder="Give us the details...",
            style=discord.TextStyle.paragraph, max_length=1000, required=True
        )
        self.evidence = discord.ui.TextInput(
            label="Proof / extra details (optional)", placeholder="Links or extra information",
            style=discord.TextStyle.paragraph, max_length=500, required=False
        )
        self.add_item(self.username)
        self.add_item(self.description)
        self.add_item(self.evidence)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        answers = {
            "Username": str(self.username),
            "Issue": str(self.description),
            "Proof / extra details": str(self.evidence) or "Not provided",
        }
        await create_ticket(interaction, self.category, answers)


class StaffApplicationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Dravixplayz Staff Application", timeout=300)
        self.position = discord.ui.TextInput(
            label="Position (choose one)", placeholder="Helper, Moderator, Admin, Developer, Support Team",
            max_length=40, required=True
        )
        self.username = discord.ui.TextInput(
            label="Minecraft / Discord username", max_length=100, required=True
        )
        self.experience = discord.ui.TextInput(
            label="Staff experience", placeholder="Tell us about your experience",
            style=discord.TextStyle.paragraph, max_length=700, required=True
        )
        self.availability = discord.ui.TextInput(
            label="Daily availability", placeholder="Example: 1–2 hours",
            max_length=100, required=True
        )
        self.reason = discord.ui.TextInput(
            label="Why should we choose you?", style=discord.TextStyle.paragraph,
            max_length=700, required=True
        )
        for item in (self.position, self.username, self.experience, self.availability, self.reason):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        allowed = {"helper", "moderator", "admin", "developer", "support team"}
        if str(self.position).strip().lower() not in allowed:
            await interaction.response.send_message(
                "Please enter one of these positions exactly: Helper, Moderator, Admin, Developer, Support Team.",
                ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        answers = {
            "Username": str(self.username),
            "Staff experience": str(self.experience),
            "Daily availability": str(self.availability),
            "Why choose you?": str(self.reason),
        }
        await create_ticket(
            interaction, "Staff Application", answers,
            application_position=str(self.position).strip()
        )


class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Player Report", description="Report a player or cheater", emoji="🛡️", value="Player Report"),
            discord.SelectOption(label="Ban Appeal", description="Appeal a punishment", emoji="⚖️", value="Ban Appeal"),
            discord.SelectOption(label="Partnership", description="Request a partnership", emoji="🤝", value="Partnership"),
            discord.SelectOption(label="General Support", description="Get help from the team", emoji="💬", value="General Support"),
            discord.SelectOption(label="Staff Application", description="Apply for a staff position", emoji="👥", value="Staff Application"),
        ]
        super().__init__(
            placeholder="Choose a ticket category...",
            min_values=1, max_values=1, options=options,
            custom_id="dravix:ticket_category"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Use this panel in the server.", ephemeral=True)
            return
        if self.values[0] == "Staff Application":
            await interaction.response.send_modal(StaffApplicationModal())
        else:
            await interaction.response.send_modal(TicketModal(self.values[0]))


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="dravix:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
            return
        await interaction.response.send_message(f"🎯 Ticket claimed by {interaction.user.mention}.")
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name[:50]}"
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="dravix:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        topic = interaction.channel.topic if isinstance(interaction.channel, discord.TextChannel) else ""
        owner_match = re.search(r"ticket_owner=(\d+)", topic or "")
        owner_id = int(owner_match.group(1)) if owner_match else None
        if interaction.user.id != owner_id and not is_staff(interaction.user):
            await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        transcript = await make_transcript(channel)
        if TRANSCRIPT_LOG_CHANNEL_ID:
            log_channel = interaction.guild.get_channel(TRANSCRIPT_LOG_CHANNEL_ID)
            if isinstance(log_channel, discord.TextChannel):
                file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}-transcript.txt")
                await log_channel.send(
                    content=f"📁 Transcript for **{channel.name}** closed by {interaction.user.mention}.",
                    file=file
                )
        await channel.edit(topic=(channel.topic or "") + "; ticket_closed=true", reason="Ticket closed")
        await channel.set_permissions(interaction.guild.default_role, view_channel=False)
        if owner_id:
            owner = interaction.guild.get_member(owner_id)
            if owner:
                await channel.set_permissions(owner, view_channel=False)
        await channel.send(embed=ticket_embed(
            "🔒 Ticket Closed",
            f"Closed by {interaction.user.mention}. Transcript was "
            + ("sent to the configured log channel." if TRANSCRIPT_LOG_CHANNEL_ID else "not saved because no transcript log channel is configured.")
            + "\nStaff can delete this channel when ready."
        ), view=ClosedControls())
        await interaction.followup.send("Ticket closed.", ephemeral=True)


class ClosedControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Delete Channel", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="dravix:delete")
    async def delete_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Only staff can delete this channel.", ephemeral=True)
            return
        await interaction.response.send_message("Deleting this ticket channel…", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")


class ApplicationReview(discord.ui.View):
    def __init__(self, applicant_id: int, ticket_channel_id: int, position: str):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.ticket_channel_id = ticket_channel_id
        self.position = position
        # These review buttons work while the bot process is running. If the bot restarts,
        # older review messages may need a fresh review panel because their per-message state
        # is not stored in a database in this starter version.

    async def finish(self, interaction: discord.Interaction, decision: str):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.response.send_message("Only authorized staff can review applications.", ephemeral=True)
            return
        result = "✅ ACCEPTED" if decision == "accepted" else "❌ REJECTED"
        embed = interaction.message.embeds[0] if interaction.message.embeds else ticket_embed("Staff Application", "")
        embed.color = discord.Color.green() if decision == "accepted" else discord.Color.red()
        embed.add_field(name="Decision", value=f"{result} by {interaction.user.mention}", inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        guild = interaction.guild
        if guild:
            member = guild.get_member(self.applicant_id)
            if member:
                try:
                    await member.send(
                        f"Your Dravixplayz staff application for **{self.position}** was **{decision}**. "
                        "If you have questions, please contact the server staff."
                    )
                except discord.HTTPException:
                    pass
            ticket = guild.get_channel(self.ticket_channel_id)
            if isinstance(ticket, discord.TextChannel):
                await ticket.send(f"📋 Staff application reviewed: **{decision.upper()}** by {interaction.user.mention}.")

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction, "accepted")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.finish(interaction, "rejected")


async def make_transcript(channel: discord.TextChannel) -> str:
    lines = [
        f"Transcript: {channel.name}",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 72,
    ]
    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.isoformat()
        content = message.clean_content or "[no text]"
        attachments = " ".join(a.url for a in message.attachments)
        lines.append(f"[{timestamp}] {message.author} ({message.author.id}): {content}")
        if attachments:
            lines.append(f"  Attachments: {attachments}")
        for embed in message.embeds:
            if embed.title or embed.description:
                lines.append(f"  Embed: {embed.title or ''} {embed.description or ''}")
    return "\n".join(lines)


@bot.event
async def on_ready():
    bot.add_view(TicketPanel())
    bot.add_view(TicketControls())
    bot.add_view(ClosedControls())
    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
    else:
        await bot.tree.sync()
    print(f"Logged in as {bot.user} — Dravixplayz ticket bot is online.")


@bot.tree.command(name="ticketpanel", description="Post the Dravixplayz private ticket panel")
async def ticketpanel(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not (
        interaction.user.guild_permissions.manage_guild or is_staff(interaction.user)
    ):
        await interaction.response.send_message("You need Manage Server permission or a configured staff role.", ephemeral=True)
        return
    embed = ticket_embed(
        "🎫 DRAVIXPLAYZ SUPPORT",
        "Welcome to **Dravixplayz Bot | pravite:1**!\n\n"
        "Select a category below to create a private ticket. Please provide accurate details so our team can help."
    )
    embed.add_field(name="🛡️ Player Report", value="Report players or cheaters", inline=True)
    embed.add_field(name="⚖️ Ban Appeal", value="Appeal a punishment", inline=True)
    embed.add_field(name="🤝 Partnership", value="Partnership requests", inline=True)
    embed.add_field(name="💬 General Support", value="Questions and help", inline=True)
    embed.add_field(name="👥 Staff Application", value="Apply for Helper, Moderator, Admin, Developer or Support Team", inline=False)
    await interaction.response.send_message(embed=embed, view=TicketPanel())


@bot.tree.command(name="ticketsetup", description="Check ticket bot configuration")
async def ticketsetup(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not (
        interaction.user.guild_permissions.manage_guild or is_staff(interaction.user)
    ):
        await interaction.response.send_message("You need Manage Server permission or a configured staff role.", ephemeral=True)
        return
    missing = []
    if not STAFF_ROLE_IDS:
        missing.append("STAFF_ROLE_IDS")
    if not APPLICATION_REVIEW_CHANNEL_ID:
        missing.append("APPLICATION_REVIEW_CHANNEL_ID")
    if not TRANSCRIPT_LOG_CHANNEL_ID:
        missing.append("TRANSCRIPT_LOG_CHANNEL_ID")
    message = "Configuration check:\n" + (
        "\n".join(f"⚠️ `{x}` is not configured." for x in missing)
        if missing else "✅ Main settings are configured."
    )
    await interaction.response.send_message(message, ephemeral=True)



if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")

bot.run(TOKEN)
