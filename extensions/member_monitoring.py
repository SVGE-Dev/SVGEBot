import logging

from discord.ext import commands


class MemberMonitoring(commands.Cog, name="Member Monitoring"):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("SVGEBot.MemberMonitoring")
        self.logger.info("Loaded MemberMonitoring")

    def cog_unload(self):
        self.logger.info("Unloaded MemberMonitoring")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        self.logger.info(f"{member.name} ({member.id}) joined {member.guild.name} "
                         f"({member.guild.id}).")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        self.logger.info(f"{member.name} ({member.id}) left {member.guild.name} "
                         f"({member.guild.id}).")


def setup(bot):
    bot.add_cog(MemberMonitoring(bot))
