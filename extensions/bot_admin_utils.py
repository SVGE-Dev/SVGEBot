from discord.ext import commands
import discord
import asyncio
import logging


class AdminUtilsCog(commands.Cog, name="Admin Utilities"):
    """Cog for administrative commands, be these for users or to manage the bot"""
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("SVGEBot.AdminUtils")
        self.delete_message_after = self.bot.bot_config["delete_msg_after"]
        self.logger.info("Loaded AdminUtils")

    async def cog_check(self, ctx):
        """This method is a cog wide check to ensure users have "admin" roles,

        It will be called without the need for check decorators on every command.
        """
        sender_is_admin = False
        for role in ctx.message.author.roles:
            if role.id in self.bot.bot_config["admin_role_id_list"]:
                sender_is_admin = True
                break
        return sender_is_admin

    def cog_unload(self):
        self.logger.info("Unloaded AdminUtils")

    @commands.command()
    async def shutdown(self, ctx):
        """Shuts the bot process down gracefully."""
        await self.bot.logout()
        self.logger.info("Logged out and closed Discord API connection")
        await asyncio.sleep(5)
        self.logger.info("Closing process")
        exit(0)


def setup(bot):
    bot.add_cog(AdminUtilsCog(bot))
