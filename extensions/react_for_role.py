import logging

from discord.ext import commands


class ReactForRole(commands.Cog, name="React for Role"):
    def __init__(self, bot):
        self.bot = bot
        self.db_conn_cog = None
        self.logger = logging.getLogger("SVGEBot.RoleReact")
        self.logger.info("Loaded ReactForRole")

    @property
    def cmd_prefix(self):
        return self.bot.bot_config['cmd_prefix']

    def cog_unload(self):
        self.logger.info("Unloaded ReactForRole")

    @commands.Cog.listener()
    async def on_ready(self):
        self.db_conn_cog = self.bot.get_cog("DBConnPool")

    async def cog_check(self, ctx):
        """This method is a cog wide check to ensure users have "admin" roles,

        It will be called without the need for check decorators on every command.
        """
        for role in ctx.message.author.roles:
            if role.id in self.bot.bot_config["admin_role_id_list"]:
                return True
        return False

    @commands.group(name="r4r")
    async def react_for_role_group(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(f"You need to use a subcommand with this command group.\n\n"
                           f"Use `{self.cmd_prefix}help r4r` to see child commands.")

    @react_for_role_group.group(name="message")
    async def r4r_message_group(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(f"You need to use a subcommand with this command group.\n\n"
                           f"Use `{self.cmd_prefix}help r4r message` to see child commands.")

    @r4r_message_group.command(name="create")
    @commands.guild_only()
    async def r4r_message_create(self, ctx, channel_id):
        guild_db_name = "guild_"+str(ctx.guild.id)
        target_channel = ctx.guild.get_channel(int(channel_id))
        if target_channel is None:
            await ctx.send("Invalid channel id, try again.", delete_after=self.bot.delete_msg_after)
            self.logger.debug(f"{ctx.author} attempted to create an rfr message but failed to "
                              f"supply a valid channel ID.")
            return
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    INSERT INTO r_for_r_messages (r_for_r_id, message_id) 
                    VALUES (DEFAULT, NULL);
                    SELECT LAST_INSERT_ID()
                """)
                current_rfr_id = await cursor.fetchone()
                await cursor.execute("""
                    CREATE TABLE %(rfr_id)s (
                        rfr_emoji_id INT
                    )
                """, {"rfr_id": f"rfr_emoji_{str(current_rfr_id)}"})

        await ctx.send(f"Created rfr with rfr identifier: {str(current_rfr_id)}.\n\n"
                       f"Do not lose this identifier, ")


def setup(bot):
    bot.add_cog(ReactForRole(bot))
