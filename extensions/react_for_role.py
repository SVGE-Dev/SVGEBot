import logging

from discord.ext import commands


class ReactForRole(commands.Cog, name="React for Role"):
    def __init__(self, bot):
        self.bot = bot
        self.db_conn_cog = None
        self.logger = logging.getLogger("SVGEBot.RoleReact")
        self.logger.info("ReactForRole loaded")

    def cog_unload(self):
        self.logger.info("Unloaded ReactForRole")

    @commands.Cog.listener()
    async def on_ready(self):
        self.db_conn_cog = self.bot.get_cog("DBConnPool")


def setup(bot):
    bot.add_cog(ReactForRole(bot))
