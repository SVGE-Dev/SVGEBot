import logging
import os
import json

import datetime

from shutil import copyfile

from discord.ext import commands, tasks


class WarningsCog(commands.Cog, name="Warning System"):
    def __init__(self, bot):
        self.bot = bot
        self.db_conn_cog = None
        self.logger = logging.getLogger("SVGEBot.WarningSystem")
        self.cog_config = None
        self.__get_config()
        self.logger.info("Loaded WarningsCog")

    @commands.Cog.listener()
    async def on_ready(self):
        self.db_conn_cog = self.bot.get_cog("DBConnPool")

    def __get_config(self, run_counter=0):
        cog_conf_location = "./extensions/extension_configs/warnings_config.json"
        default_cog_conf_loc = "./extensions/extension_configs/warningsys_config_default.json"
        if os.path.exists(cog_conf_location):
            with open(cog_conf_location) as cog_config_obj:
                self.cog_config = json.load(cog_config_obj)
        else:
            self.logger.warning("Main config not found, copying default config "
                                "and attempting to use instead.")
            copyfile(default_cog_conf_loc, cog_conf_location)
            run_counter += 1
            if run_counter < 2:
                self.__get_config(run_counter=run_counter)
            else:
                self.logger.exception(f"Default config malconfigured or otherwise missing "
                                      f"from expected location: {cog_conf_location}, and"
                                      f"{default_cog_conf_loc}. Please visit: "
                                      f"https://github.com/SVGE-Dev/SVGEBot and reacquire the "
                                      f"config files.")
                input()
                exit(1)

    async def cog_check(self, ctx):
        """This method is a cog wide check to ensure users have "admin" roles,

        It will be called without the need for check decorators on every command.
        """
        for role in ctx.message.author.roles:
            if role.id in self.bot.bot_config["admin_role_id_list"]:
                return True
        return False

    @commands.command()
    @commands.guild_only()
    async def warn(self, ctx, target_user_id, action, *, reason: str):
        """Warn a member of the guild.

        :param ctx: Command context, auto-filled by API wrapper.
        :param target_user_id: ID of the user to be warned
        :type target_user_id: int
        :param action: Action to take against warned user, for expediting
            certain bot behaviour, can be one of: kick, ban
        :type action: str
        :param reason: Reason for warning the user, this will be sent to
            the warned user.
        """
        warning_datetime = datetime.datetime.utcnow()
        target_member_object = ctx.guild.get_member(int(target_user_id))
        target_user_object = self.bot.get_user(int(target_user_id))
        mute_role_object = ctx.guild.get_role(self.cog_config["mute_role_id"][str(ctx.guild.id)])
        if target_member_object is None and target_user_object is None:
            await ctx.author.send(f"{target_user_id} is invalid, please try again.")
            return
        elif target_member_object is None and target_user_object is not None:
            await ctx.author.send(f"{target_user_id} is not a member, but is a valid "
                                  f"discord user. The bot will not send warnings to users that "
                                  f"are not members of your guild. You may change a config "
                                  f"option to alter ")
            return
        elif mute_role_object is None:
            await ctx.author.send('No mute role has been defined for the guild you are attempting '
                                  'to warn in. Add one to the `warnings_config.json` file in the '
                                  'format: `"guild_id": role_id` where `role_id` is an integer '
                                  'type and `"guild_id"` is a string type.')
            return

        guild_db_name = "guild_"+str(ctx.guild.id)

        async with self.db_conn_cog.conn_pool.acquire() as dbconn:
            async with dbconn.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    SELECT * 
                    FROM warning_table
                    WHERE warned_user_id = %(d_uid)s
                    AND expired = 0
                """, {"d_uid": target_user_id})
                user_warning_results = await cursor.fetchall()

        # Preemptively mute the warning target as this command can take a while
        await target_member_object.add_roles(mute_role_object)

        if len(user_warning_results) == 0:
            self.logger.debug("Found no active warnings on member's profile")
            await ctx.author.send("No unexpired warnings found on target profile.")

        warning_expiry_timedelta = datetime.timedelta(
            **self.cog_config["warning_expiry_time"]
        )

        prev_warning_count = 0

        for warning_result in user_warning_results:
            # Find the amount of time between the old warning and this one, if it
            # exceeds the expiry time given in
            # ./extensions/extensions_config/warnings_config.json then set it as expired and
            # ignore it.
            warning_time_delta = warning_datetime - warning_result[6]
            if warning_time_delta >= warning_expiry_timedelta:
                warning_result[1] = 1
            else:
                # In the else case, the warning is valid and should be counted
                prev_warning_count += 1

        total_valid_warnings = prev_warning_count + 1
        new_warning_entry = []


def setup(bot):
    bot.add_cog(WarningsCog(bot))
