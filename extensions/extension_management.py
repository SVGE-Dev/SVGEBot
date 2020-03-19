import logging

import discord
from discord.ext import commands


class CogManagementCog(commands.Cog, name="Extension Management"):
    """Cog for manual extension management."""
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("SVGEBot.ExtensionManagement")

    @commands.group(name="extension")
    @commands.has_permissions(administrator=True)
    async def grp_extension(self, ctx):
        """Command group for extension management, invoking without sub-commands has no effect.

        Requires:
            - administrator
        """
        if ctx.invoked_subcommand is None:
            self.logger.info("Command: {0.content} from {0.author} in {0.guild} {0.channel} "
                             "had no sub-command.".format(ctx.message))

    @grp_extension.command()
    async def load(self, ctx, extension):
        """Loads an inactive extension contained within ./extensions/

        :param extension: Extension name, omit the ".py" in the extension filename.
        """
        try:
            self.bot.load_extension(f"extensions.{extension}")
        except Exception as err:
            self.logger.warning(f"Failed to load extension {extension}:\n\n{err}")

    @grp_extension.command()
    async def unload(self, extension):
        """Unloads an active extension that is contained within ./extensions/

        :param extension: Extension name, omit the ".py" in the extension filename.
        """
        try:
            self.bot.unload_extension(f"extensions.{extension}")
        except Exception as err:
            self.logger.warning(f"Failed to unload extension {extension}:\n\n{err}")

    @grp_extension.command()
    async def reload(self, ctx, extension):
        """Reload an active extension that is contained within ./extensions/,
        this is equivalent to unloading and loading an extension, this command
        will roll back to prior state in case of an error.

        :param extension: Extension name, omit the ".py" in the extension filename.
        """
        try:
            self.bot.reload_extension(f"extensions.{extension}")
        except Exception as err:
            self.logger.warning(f"Failed to reload extension {extension}:\n\n{err}")


def setup(bot):
    bot.add_cog(CogManagementCog(bot))
