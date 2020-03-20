from discord.ext import commands

import logging


class AdminUtilsCog(commands.Cog, name="Admin Utilities"):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("SVGEBot.AdminUtils")
        self.delete_message_after = self.bot.bot_config["delete_msg_after"]

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
                

def setup(bot):
    bot.add_cog(AdminUtilsCog)
