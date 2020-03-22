from discord.ext import commands
import discord
import asyncio
import logging


class AdminUtilsCog(commands.Cog, name="Admin Utilities"):
    """Cog for administrative commands, be these for users or to manage the bot.

    All commands within this cog require administrative permissions or admin-like roles
    """
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("SVGEBot.AdminUtils")
        self.delete_message_after = self.bot.bot_config["delete_msg_after"]
        self.logger.info("Loaded AdminUtils")

    async def cog_check(self, ctx):
        """This method is a cog wide check to ensure users have "admin" roles,

        It will be called without the need for check decorators on every command.
        """
        for role in ctx.message.author.roles:
            if role.id in self.bot.bot_config["admin_role_id_list"]:
                return True
        return False

    def cog_unload(self):
        self.logger.info("Unloaded AdminUtils")

    @commands.command()
    async def shutdown(self, ctx):
        """Shuts the bot process down gracefully."""
        await ctx.send(":wave:", delete_after=1)
        await asyncio.sleep(2)
        await self.bot.logout()
        self.logger.info("Logged out and closed Discord API connection")
        self.logger.info("Closing process")
        # This sleep is to avoid background loops getting messed with by an
        # abrupt exit.
        await asyncio.sleep(4)
        exit(0)

    @commands.command()
    async def change_presence(self, ctx, activity, text=">>help"):
        """Changes the bot "presence" statement to that defined in command,
        permitting it is one of those permitted by discord.

        Command originally written for CyclopsBot by JayDwee.

        :arg ctx: Command context, auto-filled by API wrapper.
        :arg activity: Activity for the bot to display, must be one of:

        :arg text: Text following the activity term"""

        activity_list = {
            "watching": discord.ActivityType.watching,
            "streaming": discord.ActivityType.streaming,
            "playing": discord.ActivityType.playing,
            "listening": discord.ActivityType.listening
        }

        if activity.lower() not in activity_list.keys():
            await ctx.send(f'"{activity}" is an invalid activity. "WatchingW, "streaming", '
                           f'"playing", and "listening" are currently supported',
                           delete_after=self.bot.delete_msg_after)
            return

        activity_type_to_show = discord.Activity(activity=activity_list[activity.lower()],
                                                 name=text)
        await self.bot.change_presence(activity=activity_type_to_show)
        self.logger.info(f"Activity changed to {activity} {text}")
        await ctx.send(f"Activity changed as requested.", delete_after=self.bot.delete_msg_after)


def setup(bot):
    bot.add_cog(AdminUtilsCog(bot))
