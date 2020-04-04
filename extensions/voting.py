import logging

from discord.ext import commands


class VotingCog(commands.Cog, name="Voting"):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("SVGEBot.logging")


def setup(bot):
    bot.add_cog(VotingCog(bot))
