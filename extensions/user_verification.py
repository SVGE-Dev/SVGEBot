import logging
import re
import os
import json
import datetime
import hashlib
import asyncio
from typing import Union
from shutil import copyfile
from email.message import EmailMessage

import aiosmtplib
import discord
from discord.ext import commands


class UserVerification(commands.Cog, name="User Verification"):
    def __init__(self, bot):
        self.bot = bot
        self.db_pool_cog = None
        self.__cog_config = None
        self.__get_config()
        self.logger = logging.getLogger("SVGEBot.Verification")
        self.logger.info("Loaded User Verification")

    @property
    def cmd_prefix(self):
        return self.bot.bot_config['cmd_prefix']

    def __get_config(self, run_counter=0):
        # This function needs to be moved into a shared extension, and likely will be soon.
        # Currently a duplicate exists within ./extensions/db_conn.py
        cog_conf_location = "./extensions/extension_configs/user_verification_config.json"
        default_cog_conf_loc = "./extensions/extension_configs/user_verification_config_default" \
                               ".json"
        if os.path.exists(cog_conf_location):
            with open(cog_conf_location) as cog_config_obj:
                self.__cog_config = json.load(cog_config_obj)
        else:
            self.logger.warning("Main config not found, copying default config"
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

    @commands.Cog.listener()
    async def on_ready(self):
        # Get the database connection pool cog
        self.db_pool_cog = self.bot.get_cog("DBConnPool")

    async def __add_member_to_database(self, member):
        """Adds a member to their respective guild database

        :param member: Member object to have details entered into the database
        :type member: discord.Member
        :returns: Whether or not the member already existed
        :rtype: bool"""
        guild_db_name = "guild_" + str(member.guild.id)
        async with self.db_pool_cog.conn_pool.acquire() as db_conn:
            async with db_conn.cursor() as cursor:
                await cursor.execute("USE `%s`", (guild_db_name,))
                # Create the user records, very old datetime used to ensure the first
                # verification attempt will be allowed. There is a one minute ratelimit
                # on verification request having emails, rather than on the command scale.
                await cursor.execute(
                    """SELECT discord_user_id
                    FROM guild_members
                    WHERE discord_user_id = %s""",
                    (member.id,)
                )
                user_already_exists = bool(await cursor.fetchall())

                await cursor.execute(
                    """INSERT INTO guild_members (
                        discord_user_id, discord_username, verified
                    ) VALUES (%(d_uid)s, %(d_uname)s, 0) 
                    ON DUPLICATE KEY UPDATE discord_username=(%(d_uname)s);
                    INSERT INTO member_verification (
                        discord_user_id, email, verification_key, last_verification_req
                    ) VALUES (%(d_uid)s, NULL, '', %(datetime_old)s)
                    ON DUPLICATE KEY UPDATE discord_user_id=discord_user_id""",
                    {"d_uid": member.id, "d_uname": member.name+"#"+member.discriminator,
                     "datetime_old": datetime.datetime(2000, 1, 1)}
                )

                return user_already_exists

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # The code contained in this conditional will execute only if the member does
        # not already exist in the guild database
        if not await self.__add_member_to_database(member):
            try:
                try:
                    # Tries to pull the guild alias from the auto-generated reversed
                    # hashtable, if this fails, just uses the guild ID instead
                    guild_alias = self.db_pool_cog.cog_config["guild_aliases_reversed"][
                        member.guild.id]
                except KeyError:
                    guild_alias = member.guild.id
                await member.send(
                    f"Welcome to {member.guild.name}.\n\n"
                    f"{member.guild.name} only allows verified accounts to join. "
                    f"In the case that you are an alumni, please contact a member "
                    f"of the administrative team for assistance.\n\n"
                    f"In order to verify, you will need to provide a University of "
                    f"Southampton (@soton.ac.uk) email address in this DM.\n\n"
                    f"The command will look like:\n"
                    f"`{self.cmd_prefix}verify email <your_soton_email_address> {guild_alias}`."
                )
            except commands.errors.CommandInvokeError:
                self.logger.warning(f"Unable to message {member.name}")

    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    @commands.command()
    async def bulk_member_registration(self, ctx):
        """Bulk member scan command that will ingest all members that are not yet in
        the guild database. Sends a similar message to on_member_join listener."""
        for member in ctx.guild.members:
            if not await self.__add_member_to_database(member):
                try:
                    try:
                        guild_alias = self.db_pool_cog.cog_config["guild_aliases_reversed"][
                            member.guild.id]
                    except KeyError:
                        guild_alias = member.guild.id
                    await member.send(
                        f"You have been added to {member.guild.name}'s verification system. "
                        f"In order to verify, you will need to provide a University of "
                        f"Southampton (@soton.ac.uk) email address in this DM.\n\n"
                        f"The command will look something like:\n"
                        f"`{self.cmd_prefix}verify email <your_soton_email_address> {guild_alias}`."
                    )
                except commands.errors.CommandInvokeError:
                    self.logger.warning(f"Unable to message {member.name}")
                    await ctx.send(f"Unable to DM verification instructions to: {member.name} "
                                   f"({member.id}).")

    async def __generate_user_verification_code(self, guild_alias, *ingredients):
        """Generates a user verification code based on a guild alias and
        a series of ingredients for uniqueness. Internally uses a sha256 hash algorithm
        and takes a subset of characters to attempt enforcement of uniqueness.

        :param guild_alias: Guild alias to be suffixed to verification code
        :type guild_alias: str
        :param ingredients: any number of hash ingredients to generate uniqueness between
            users. It is recommended that you include some time dependence on top of any other
            ingredients to ensure that the same user will get completely different results
            on each usage.
        :return: Verification code string of the format:
            VERIFY-90JE2D-2O9A0P-9J02N2-GUILD_ALIAS
        :rtype: str

        todo:
            - Might be worth having one section of the code be entirely
            datetime.datetime.now() based to enforce true uniqueness, though this isn't
            too high on the priority list.
        """
        code_length = 21
        bytes_ingredient_string = str(ingredients)
        hash_raw_hex_out = hashlib.sha256(bytes_ingredient_string.encode()).hexdigest()
        if code_length > len(hash_raw_hex_out):
            self.logger.error("Defined code_length longer than length of sha256 hash.")
            return None
        out_string = ""
        for char_index in range(1, code_length):
            if char_index % 7 == 0:
                out_string += "-"
            else:
                out_string += hash_raw_hex_out[char_index-1]
        return "VERIFY-"+out_string.upper()+"-"+guild_alias.upper()

    @commands.group()
    @commands.dm_only()
    @commands.max_concurrency(1, per=commands.BucketType.user)
    async def verify(self, ctx):
        """Command group for user verification, does nothing when invoked
        directly."""
        if ctx.invoked_subcommand is None:
            await ctx.send(f"You need to use a subcommand with this command group.\n\n"
                           f"Use `{self.cmd_prefix}help verify to see child commands.")

    async def __gen_guild_id_from_verification_code(self, ctx, verification_code):
        """Pulls the related guild id from a verification_code, which contains a guild
        alias after the final hyphen.

        :param ctx: command context
        :type ctx: commands.Context
        :param verification_code: Verification code string to pull guild information from
        :type verification_code: str
        :return: Guild ID in string form or None in the case of no ID being found.
        :rtype: Union[str, NoneType]"""
        guild_alias = verification_code.rsplit("-", 1)[-1]
        try:
            guild_id = str(self.db_pool_cog.cog_config["guild_aliases"][guild_alias.lower()])
        except KeyError:
            self.logger.debug(f"Failed to generate guild_id from alias: {verification_code},"
                              f" {guild_alias}.")
            await ctx.send("The verification code you submitted was invalid, ensure you "
                           "include hyphens in the code.")
            return None
        return guild_id

    async def __member_verify_update(self, target_user_id, guild_id):
        """Attempt to give the verification role to target_user_id's corresponding
        member object in the guild represented by guild_id.

        :param target_user_id: discord user ID for a member in guild represented by
            guild_id
        :type target_user_id: Union[int, str]
        :param guild_id: guild ID for guild member represented by `target_user_id`
        :type guild_id: Union[int, str]
        :return: In case of failure, will return False, in case of success, will return True.
        :rtype: bool"""
        guild_object = self.bot.get_guild(int(guild_id))
        member_object = guild_object.get_member(int(target_user_id))
        role_id = self.__cog_config["discord"]["verified_role_ids"][str(guild_id)]
        try:
            verify_role_object = guild_object.get_role(role_id)
        except KeyError:
            self.logger.exception("Unable to find verification role.")
            return False
        if verify_role_object is None:
            self.logger.exception(f"Couldn't find role {role_id}")
            return False

        try:
            await member_object.add_roles(verify_role_object)
            return True
        except discord.HTTPException as add_role_error:
            self.logger.exception(f"Role addition failed during automated verification. Error: "
                                  f"\n\n{add_role_error}")
            return False

    @verify.command()
    async def code(self, ctx, verification_code):
        """Verify the code you were emailed, ensure you send the entire code as give in your
        email, as it carries information this bot needs to confirm various context elements of
        verification.

        :param ctx: Command context, automatically filled, do not pass.
        :type ctx: commands.Context
        :param verification_code: Verification code, will take a form like:
            VERIFY-90JE2D-2O9A0P-9J02N2-GUILD_ALIAS
        :type verification_code: str"""
        guild_id = await self.__gen_guild_id_from_verification_code(ctx, verification_code)
        async with self.db_pool_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                guild_db_name = "guild_"+str(guild_id)
                await cursor.execute("""USE `%s`""", guild_db_name)
                await cursor.execute(
                    """SELECT *
                    FROM guild_members
                    WHERE discord_user_id = %(d_uid)s""",
                    {"d_uid": ctx.author.id})
                guild_member_result = list(await cursor.fetchone())
                await cursor.execute(
                    """SELECT *
                    FROM member_verification
                    WHERE discord_user_id = %(d_uid)s""",
                    {"d_uid": ctx.author.id}
                )
                verification_query_result = list(await cursor.fetchone())
                self.logger.debug(f"User records found: \n{guild_member_result}"
                                  f"\n{verification_query_result}")
                if guild_member_result[3] == 1:
                    self.logger.debug(f"Code verification for {ctx.author} failed due "
                                      f"to user already validated.")
                    await ctx.send("You have already been verified, if you think "
                                   "this was in error, please contact an administrator.")
                    return
                if verification_code != verification_query_result[2]:
                    self.logger.debug(f"Code verification for {ctx.author} failed due "
                                      f"to improper verification code.")
                    await ctx.send("The verification code you have provided is invalid, "
                                   "please ensure you use an exact copy from the "
                                   "email you were sent. ")
                    return

                # At this point we know we have a valid verification request, let's
                # update the user's record and null their verification key.
                await cursor.execute(
                    """UPDATE guild_members 
                    SET verified = 1
                    WHERE discord_user_id = %(d_uid)s;
                    UPDATE member_verification
                    SET verification_key = NULL
                    WHERE discord_user_id = %(d_uid)s""",
                    {"d_uid": ctx.author.id}
                )

                verification_success = await self.__member_verify_update(ctx.author.id, guild_id)
                if not verification_success:
                    await ctx.send(f"Verification role allocation failed. Please contact an "
                                   f"administrator with the datetime of this exception: "
                                   f"{datetime.datetime.now()}")
                else:
                    await ctx.send(f'Verification success, welcome to '
                                   f'{verification_code.rsplit("-", 1)[-1]}')

    async def send_verification_code_email(self, ctx, verification_code, curr_try=0):
        """Outsourced method to send email to a user with details in the database

        :param ctx: Calling command context
        :type ctx: discord.Context
        :param verification_code: Verification code to send, will take a form like:
            VERIFY-90JE2D-2O9A0P-9J02N2-GUILD_ALIAS
        :type verification_code: str
        :param curr_try: kwarg for lazy recursive implementation of email sending in
            case of failure. Do not pass."""
        guild_id = await self.__gen_guild_id_from_verification_code(ctx, verification_code)
        guild_alias = verification_code.rsplit("-", 1)[-1]
        guild_db_name = "guild_" + str(guild_id)

        async with self.db_pool_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                # Pull the user email address from the guild database we want to use
                await cursor.execute("""USE `%s`""", (guild_db_name,))
                await cursor.execute(
                    """SELECT email
                    FROM member_verification
                    WHERE discord_user_id=%s""",
                    (ctx.author.id,)
                )
                user_email_address = await cursor.fetchone()

        # In the case that this command has been invoked too early, try again after
        # an increasing sleep time
        if user_email_address is None and curr_try < 5:
            curr_try += 1
            self.logger.debug("Retrying email address fetching")
            await asyncio.sleep(curr_try)
            await self.send_verification_code_email(ctx, verification_code,
                                                    curr_try=curr_try)
            return
        # Generate email headers and content within a python email object
        formatted_bot_email_addr = f"{self.__cog_config['account']['username']}@gmail.com"
        formatted_bot_email_subj = f"No Reply | {guild_alias.upper()} Automated Verification Email"
        email = EmailMessage()
        email["From"] = formatted_bot_email_addr
        email["To"] = user_email_address
        email["Subject"] = formatted_bot_email_subj
        email.set_content(
            f"Hi {ctx.author},\n\nThis is an automated verification "
            f"email for {guild_alias.upper()}, if you did not request "
            f"verification, please notify an administrator.\n\n"
            f"In order to use your verification code, send the following "
            f"command in direct messages to {self.bot.user}:\n"
            f"{self.cmd_prefix}verify code {verification_code}\n\n"
            f"Please contact an administrator if you have trouble with "
            f"this command.\n\nIn the case that you have made multiple email "
            f"requests and this is the first to be received, the code given "
            f"here will no longer be valid and you will need to wait for "
            f"the final in your request chain.\n\n"
            f"This email address is not monitored."
        )

        try:
            # Attempt to send the email, save the response in `response`
            async with aiosmtplib.SMTP(**self.__cog_config["email"], **self.__cog_config[
                    "account"]) as smtp_client:
                response = await smtp_client.send_message(email)
        except aiosmtplib.SMTPException as smtp_error:
            self.logger.exception(f"Unable to complete email operation:\n\n{smtp_error}")
            await ctx.send(f"There was an exception while sending your verification email, "
                           f"please contact an administrator with the date and time of this "
                           f"error: `{datetime.datetime.now()}`")
            return

        # Let the user know that their email has been sent
        self.logger.debug(f"Sent email to {user_email_address}, response: {response}.")
        await ctx.send(f"A verification email has been sent to you from: "
                       f"{formatted_bot_email_addr}, with the subject: "
                       f"{formatted_bot_email_subj}. \n\n"
                       f"Please make sure to check your spam and filter mailboxes, "
                       f"if you have not received an email after a couple of minutes, "
                       f"check whether the email address you have entered is correct. "
                       f"If you have entered a correct email, attempt this command "
                       f"again at least two minutes after your initial attempt.")

    @verify.command()
    async def email(self, ctx, email_address, pre_verification_id):
        """Allows users to begin the verification process in a given discord guild.

        :param ctx: Command context, internally provided.
        :param email_address: Email address, must satisfy given conditions.
        :type email_address: str
        :param pre_verification_id: ID required to start verification process.
        :type pre_verification_id: Union[int, str]"""
        # Find the guild ID, either from a guild alias pre_verification_id
        # or from a guild ID pre_verification_id
        try:
            guild_id = int(pre_verification_id)
        except ValueError:
            try:
                guild_id = int(self.db_pool_cog.cog_config["guild_aliases"][
                    pre_verification_id.lower()])
            except KeyError:
                await ctx.send("You have provided an invalid pre-verification id.")
                return

        soton_email_regex_pattern = r"[\w\.]{3,64}\@soton\.ac\.uk$"
        # Check whether email address meets soton regex standards (these are super lax).
        regex_result = re.search(soton_email_regex_pattern, email_address)
        if regex_result is None:
            await ctx.send("You have provided an invalid email address. Please "
                           "make sure you submit a valid @soton.ac.uk email address")
            return

        command_datetime = datetime.datetime.now()
        # Generate the guild's table name and find it if it exists
        guild_table_name = "guild_"+str(guild_id)+""
        async with self.db_pool_cog.conn_pool.acquire() as loc_connection:
            async with loc_connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT SCHEMA_NAME
                        FROM information_schema.SCHEMATA
                        WHERE SCHEMA_NAME = %s""",
                    ("'"+guild_table_name+"'",)
                )
                table_exists = bool(await cursor.fetchall())
                # If it doesn't, let the user know they've used the wrong ID.
                if not table_exists:
                    await ctx.send("You have provided an invalid pre-verification id.")
                    return
                # Now we have a correct guild table name, USE it and begin the important
                # operations
                await cursor.execute("USE `%s`", (guild_table_name,))
                await cursor.execute(
                    """SELECT * 
                    FROM guild_members
                    WHERE discord_user_id = %s""",
                    (ctx.author.id,)
                )
                # Pull user record from guild_members and check whether they have been
                # verified
                user_result = await cursor.fetchone()
                self.logger.debug(f"User member record found: {user_result}")

                if user_result[3] == 1:
                    await ctx.send("You have already been verified, if you think "
                                   "this was in error, please contact an administrator.")
                    return

                # If everything appears correct, pull their verification record
                await cursor.execute(
                    """SELECT *
                    FROM member_verification
                    WHERE discord_user_id = %s""",
                    (ctx.author.id,)
                )
                user_verification_result = list(await cursor.fetchone())
                self.logger.debug(f"User verification record found: {user_verification_result}")
                # Check to make sure they haven't been spamming the command, this rate limit is
                # going to be moved into a config soon.
                verification_request_timediff = command_datetime - user_verification_result[3]
                if verification_request_timediff < datetime.timedelta(minutes=2):
                    await ctx.send("Please wait before attempting to resend this command, "
                                   "if you have been waiting over 15 minutes for your "
                                   "verification email, please contact an administrator.")
                    return

                # Apply changes to verification record, create verification code
                # and put it into the database
                user_verification_result[3] = command_datetime
                verification_code = await self.__generate_user_verification_code(
                    self.db_pool_cog.cog_config["guild_aliases_reversed"][guild_id],
                    str(command_datetime), str(ctx.author.id), guild_table_name
                )
                self.logger.debug(f"Generated verification code: {verification_code}.")
                user_verification_result[2] = verification_code
                user_verification_result[1] = email_address

                await cursor.execute(
                    """REPLACE INTO member_verification (
                            discord_user_id, email, verification_key, last_verification_req
                        ) VALUES (%s, %s, %s, %s)""",
                    tuple(user_verification_result)
                )
        # Now close the db connection and send email to the user
        await self.send_verification_code_email(ctx, verification_code)


def setup(bot):
    bot.add_cog(UserVerification(bot))

