import json
import logging
import os
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("coin_bot")

load_dotenv()

DATA_FILE = Path(__file__).with_name("coins.json")
COIN_CHANNEL_ID = os.getenv("COIN_CHANNEL_ID", "").strip()


def load_coins() -> dict[str, int]:
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            stored_coins = json.load(file)
        if not isinstance(stored_coins, dict):
            raise ValueError("Coin data must be a JSON object")
        return {
            str(user_id): amount
            for user_id, amount in stored_coins.items()
            if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0
        }
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError, ValueError):
        logger.exception("Could not load %s; starting with an empty coin balance", DATA_FILE)
        return {}


coins = load_coins()


def save_coins() -> None:
    temporary_file = DATA_FILE.with_suffix(".tmp")
    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(coins, file, indent=2)
    temporary_file.replace(DATA_FILE)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook() -> None:
    synced = await bot.tree.sync()
    logger.info("Synced %s application commands", len(synced))


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)
    print("KODARI_READY", flush=True)


@bot.event
async def on_message(message: discord.Message) -> None:
    if not message.author.bot and message.guild is not None and message.content.strip():
        is_coin_channel = (
            str(message.channel.id) == COIN_CHANNEL_ID
            if COIN_CHANNEL_ID
            else message.channel.name == "general"
        )
        if is_coin_channel:
            user_id = str(message.author.id)
            coins[user_id] = coins.get(user_id, 0) + 1
            try:
                save_coins()
            except OSError:
                logger.exception("Could not save coin balance for user %s", user_id)

    await bot.process_commands(message)


def coins_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.gold())


async def can_manage_coins(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False

    permissions = member.guild_permissions
    if permissions.administrator or permissions.manage_guild:
        return True

    allowed_role_names = {"moderator", "mod", "admin", "administrator"}
    return any(role.name.casefold() in allowed_role_names for role in member.roles)


@bot.tree.command(name="profile", description="View your coin balance or another user's profile")
@app_commands.describe(user="The user whose profile you want to view")
async def profile(interaction: discord.Interaction, user: Optional[discord.User] = None) -> None:
    target = user or interaction.user
    balance = coins.get(str(target.id), 0)
    embed = coins_embed("👤 User Profile", f"🪙 Coins Balance: {balance}")
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="addcoins", description="Add coins to a server member")
@app_commands.guild_only()
@app_commands.check(can_manage_coins)
@app_commands.describe(user="The member receiving coins", amount="The number of coins to add")
@app_commands.choices(amount=[])
async def addcoins(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1000000000]) -> None:
    user_id = str(user.id)
    coins[user_id] = coins.get(user_id, 0) + amount
    try:
        save_coins()
    except OSError:
        logger.exception("Could not save coin balance for user %s", user_id)
        await interaction.response.send_message(
            embed=coins_embed("⚠️ Save Failed", "The balance could not be saved. Please try again."),
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=coins_embed("✅ Coins Added", f"Added **{amount}** 🪙 to {user.mention}. They now have **{coins[user_id]}** coins.")
    )


@bot.tree.command(name="removecoins", description="Remove coins from a server member")
@app_commands.guild_only()
@app_commands.check(can_manage_coins)
@app_commands.describe(user="The member losing coins", amount="The number of coins to remove")
async def removecoins(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, 1000000000]) -> None:
    user_id = str(user.id)
    current_balance = coins.get(user_id, 0)
    removed = min(amount, current_balance)
    coins[user_id] = current_balance - removed
    try:
        save_coins()
    except OSError:
        logger.exception("Could not save coin balance for user %s", user_id)
        await interaction.response.send_message(
            embed=coins_embed("⚠️ Save Failed", "The balance could not be saved. Please try again."),
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=coins_embed(
            "✅ Coins Removed",
            f"Removed **{removed}** 🪙 from {user.mention}. They now have **{coins[user_id]}** coins.",
        )
    )


@bot.tree.command(name="leaderboard", description="Show the top 10 coin balances in this server")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            embed=coins_embed("⚠️ Server Only", "Use this command in a server."),
            ephemeral=True,
        )
        return

    members = (member for member in guild.members if not member.bot)
    ranked_members = sorted(
        members,
        key=lambda member: coins.get(str(member.id), 0),
        reverse=True,
    )[:10]

    if not ranked_members:
        description = "No member balances to show yet."
    else:
        description = "\n".join(
            f"**{rank}.** {member.mention} — 🪙 **{coins.get(str(member.id), 0)}**"
            for rank, member in enumerate(ranked_members, start=1)
        )

    await interaction.response.send_message(embed=coins_embed("🏆 Coin Leaderboard", description))


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need administrator permissions to use this command."
    elif isinstance(error, app_commands.CheckFailure):
        message = "❌ You do not have permission to use this command."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "This command can only be used in a server."
    else:
        logger.error("Application command failed", exc_info=(type(error), error, error.__traceback__))
        message = "Something went wrong while running that command. Please try again."

    embed = coins_embed("⚠️ Command Error", message)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


token = os.getenv("DISCORD_TOKEN")
if not token or token == "your_discord_bot_token_here":
    raise RuntimeError("Set DISCORD_TOKEN in your .env file before starting the bot.")

bot.run(token)