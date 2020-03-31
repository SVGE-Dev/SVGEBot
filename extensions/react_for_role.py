import math
import logging

from typing import Union, Optional, List, Tuple

import discord
from discord.ext import commands


class ReactForRole(commands.Cog, name="React for Role"):
    def __init__(self, bot):
        self.bot = bot
        self.inferred_rfr_ids = {}
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

    @staticmethod
    def get_rfr_table_name(rfr_id):
        return f"rfr_emoji_{str(rfr_id)}"

    async def cog_check(self, ctx):
        """This method is a cog wide check to ensure users have "admin" roles,

        It will be called without the need for check decorators on every command.
        """
        for role in ctx.message.author.roles:
            if role.id in self.bot.bot_config["admin_role_id_list"]:
                return True
        return False

    async def __check_if_rfr_table_exists(self, guild_db_name, rfr_msg_id):
        """Function to check whether a given rfr_emoji table exists or not.

        :param guild_db_name: Name of guild database
        :param rfr_msg_id: ID of rfr message table to search for

        :returns: Boolean, whether or not table exists
        :rtype: bool"""
        async with self.db_conn_cog.conn_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT count(*)
                    FROM information_schema.TABLES
                    WHERE (TABLE_SCHEMA = %(db_name)s) AND (TABLE_NAME = %(table_name)s)
                """, {
                    "db_name": guild_db_name,
                    "table_name": self.get_rfr_table_name(rfr_msg_id)
                })
                return bool(await cursor.fetchone())

    @commands.group(name="rfr")
    async def react_for_role_group(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(f"You need to use a subcommand with this command group.\n\n"
                           f"Use `{self.cmd_prefix}help rfr` to see child commands.")

    @react_for_role_group.group(name="message")
    async def rfr_message_group(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send(f"You need to use a subcommand with this command group.\n\n"
                           f"Use `{self.cmd_prefix}help rfr message` to see child commands.")

    @react_for_role_group.command(name="infer_id")
    @commands.guild_only()
    async def rfr_infer_id(self, ctx, id_to_infer):
        """Guild only command to set the inferred rfr_key for this guild."""
        if not await self.__check_if_rfr_table_exists("guild_"+str(ctx.guild.id), id_to_infer):
            await ctx.send("Invalid id supplied.")
            return None
        self.inferred_rfr_ids[ctx.guild.id] = id_to_infer
        return id_to_infer

    @rfr_message_group.command(name="test")
    @commands.guild_only()
    async def rfr_message_test(self, ctx, rfr_rel_id: Optional[str], target_channel: Optional[
            discord.TextChannel]):
        """Create an rfr test message in the channel """
        # if channel_id is None:
        #     target_channel = ctx.channel
        # else:
        #     target_channel = ctx.guild.get_channel(int(channel_id))
        #     if target_channel is None:
        #         await ctx.send("Invalid channel id, try again.",
        #                        delete_after=self.bot.delete_msg_after)
        #         self.logger.debug(f"{ctx.author} attempted to create an rfr message "
        #                           "but failed to supply a valid channel ID.")
        #         return
        guild_db_name = "guild_"+str(ctx.guild.id)
        if target_channel is None:
            target_channel = ctx.channel

        if rfr_rel_id is None:
            rfr_rel_id = self.inferred_rfr_ids[ctx.guild.id]
        else:
            await self.rfr_infer_id(ctx, rfr_rel_id)

        if not await self.__check_if_rfr_table_exists(guild_db_name, rfr_rel_id):
            await ctx.send("You have supplied an invalid rfr identifier, please try "
                           "again", delete_after=self.bot.delete_msg_after)
            return

        await self.__create_rfr(rfr_rel_id, guild_db_name, target_channel, ctx.guild)

    async def __create_rfr(self, rfr_id, guild_db_name, target_channel: discord.TextChannel,
                           guild: discord.Guild):
        role_emoji_name_tuple_list = await self.__get_all_rfr_relations(rfr_id, guild_db_name)
        # Because each message can only support up to a maximum of 20 reactions, we must split
        # the rfr into separate embeds and messages as required.
        num_fields_required = len(role_emoji_name_tuple_list)
        num_embeds_required = math.ceil(num_fields_required / 20)
        rfr_list_offset = 0
        embed_list = []  # type: List
        for i in range(num_embeds_required):
            embed_list.append((await target_channel.send("RFR Placeholder"), None))

        for i in range(len(embed_list)):
            new_embed = discord.Embed(title=f"SVGEBot RfR ({i+1}/{num_embeds_required})",
                                      description="React for Role Interface",
                                      colour=0x6b2b2b)
            for j in range(num_fields_required):
                j += 1
                if j > 20:
                    num_fields_required -= 20
                    rfr_list_offset += 20
                    break
                curr_tuple = role_emoji_name_tuple_list[rfr_list_offset + j - 1]
                curr_emoji = self.bot.get_emoji(int(curr_tuple[1]))
                new_embed.add_field(name=curr_tuple[2],
                                    value=f"{curr_emoji} "
                                          f"{guild.get_role(int(curr_tuple[0])).mention}",
                                    inline=True)
                await embed_list[i][0].add_reaction(curr_emoji)
            try:
                # Use the footers to link a series of many react for messages
                new_embed.set_footer(text=f"{rfr_id}_{embed_list[i+1][0].id}")
            except IndexError:
                # In the case of this being the last embed, denote it with "RFR-End"
                new_embed.set_footer(text=f"{rfr_id}_RFR-End")
            embed_list[i] = (embed_list[i][0], new_embed)
            await embed_list[i][0].edit(content=None, embed=new_embed)
        return embed_list

    async def __get_all_rfr_relations(self, rfr_id, guild_db_name):
        """Returns all rfr data linked to a given rfr_id, in a List[Tuple[str, str, str]] form.

        :param rfr_id: React for role relation ID
        :param guild_db_name: Name of guild database to access

        :return: List of Tuple of role_id, emoji_id, name
        :rtype: List[Tuple[str, str, str]]
        """
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    SELECT role_id, emoji_id, name
                    FROM r_for_r_emoji
                    INNER JOIN r_for_r_emoji_to_message
                    ON r_for_r_emoji.role_emoji_relation_id = 
                    r_for_r_emoji_to_message.role_emoji_relation_id
                """)
                return await cursor.fetchall()

    @rfr_message_group.command(name="create")
    @commands.guild_only()
    async def rfr_message_create(self, ctx):
        """Create the backend structures required for a new rfr message set"""
        guild_db_name = "guild_"+str(ctx.guild.id)
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    INSERT INTO r_for_r_messages (rfr_message_id, channel_message_id) 
                    VALUES (NULL, NULL);
                """)
                await cursor.execute("SELECT LAST_INSERT_ID();")
                current_rfr_id = (await cursor.fetchone())[0]
                await cursor.execute("""
                    CREATE TABLE `%(rfr_id)s` (
                        rfr_relation_id INT UNIQUE
                    )
                """, {"rfr_id": self.get_rfr_table_name(current_rfr_id)})

        await ctx.send(f"Created rfr with id: {str(current_rfr_id)}. "
                       f"Do not lose this identifier.")

    async def __create_new_rfr_relation(self, emoji_id, role_id, rfr_name, ctx):
        """Create a new rfr record.

        :param emoji_id: Emoji ID
        :param role_id: Role ID
        :param rfr_name: Name of rfr relation
        :param ctx: Command context

        :return: rfr emoji role relation_id or None.
        :rtype: Union[int, NoneType]"""
        guild_db_name = "guild_"+str(ctx.guild.id)
        emoji_object = self.bot.get_emoji(int(emoji_id))
        if emoji_object is None:
            await ctx.send("Invalid emoji ID supplied.", delete_after=self.bot.delete_msg_after)
            return None
        role_object = self.bot.get_guild(int(ctx.guild.id)).get_role(int(role_id))
        if role_object is None:
            await ctx.send("Invalid role ID supplied.", delete_after=self.bot.delete_msg_after)
            return None
        
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    INSERT INTO r_for_r_emoji
                    (role_emoji_relation_id, role_id, emoji_id, name) VALUES 
                    (DEFAULT, %(role_id)s, %(emoji_id)s, %(rfr_name)s);
                """, {"role_id": role_id, "emoji_id": emoji_id, "rfr_name": rfr_name})
                await cursor.execute("SELECT LAST_INSERT_ID()")
                return await cursor.fetchone()

    @rfr_message_group.command(name="add")
    @commands.guild_only()
    async def rfr_message_add_rfr(self, ctx, rfr_msg_id: str, emoji: Union[discord.Emoji, str],
                                  role: discord.Role, *, rfr_name: str):
        """Add either a pre-existing rfr relation or a new rfr relation
        to a preexisting rfr message.

        :param ctx: Auto-filled by library
        :param rfr_msg_id: ID of rfr message to add an emoji to
        :param emoji: Emoji to add to rfr
        :param role: Role to add to rfr
        :param rfr_name: Name of rfr relation
        """
        guild_db_name = "guild_"+str(ctx.guild.id)
        if type(emoji) is str:
            emoji = await commands.EmojiConverter().convert(ctx, emoji.strip(":"))

        role_emoji_relation_id = await self.__create_new_rfr_relation(emoji.id, role.id,
                                                                      rfr_name, ctx)
        # Check whether or not the addressed rfr message exists
        if not bool(await self.__check_if_rfr_table_exists(guild_db_name, rfr_msg_id)):
            await ctx.send("You have supplied an invalid rfr identifier, please try "
                           "again", delete_after=self.bot.delete_msg_after)
            return

        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    INSERT INTO r_for_r_emoji_to_message 
                    (rfr_message_id, role_emoji_relation_id)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE rfr_message_id=rfr_message_id
                """, (rfr_msg_id, role_emoji_relation_id,))
        await ctx.send(f'Added emoji: "{emoji}" to rfr '
                       f'message with ID: "{rfr_msg_id}." To test how this message will look, '
                       f'send: `>>rfr message test {rfr_msg_id}` in a private channel.',
                       delete_after=self.bot.delete_msg_after)


def setup(bot):
    bot.add_cog(ReactForRole(bot))
