import math
import re
import logging
import asyncio

from typing import Union, Optional, List, Tuple

import discord
from discord.ext import commands


class ReactForRole(commands.Cog, name="React for Role"):
    def __init__(self, bot):
        self.bot = bot
        self.inferred_rfr_ids = {}
        self.rfr_message_sets = {}
        self.db_conn_cog = None
        self.logger = logging.getLogger("SVGEBot.RoleReact")
        self.logger.info("Loaded ReactForRole")

    @property
    def cmd_prefix(self):
        return self.bot.bot_config['cmd_prefix']

    def cog_unload(self):
        self.logger.info("Unloaded ReactForRole")

    async def cog_after_invoke(self, ctx):
        if ctx.guild is not None and ctx.invoked_subcommand is None:
            rfr_id_used = self.inferred_rfr_ids[ctx.guild.id]
            await self.__update_rfr_embeds(ctx, rfr_id_used)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        user = await self.bot.fetch_user(payload.user_id)
        if user.bot:
            return

        channel = await self.bot.fetch_channel(payload.channel_id)
        guild = channel.guild
        rfr_emoji_list = await self.__gather_rfr_emoji(guild)
        if str(payload.emoji.id) not in rfr_emoji_list:
            return

        reaction_message = await channel.fetch_message(payload.message_id)
        reaction_rfr_id = None
        for rfr_id, message_set in self.rfr_message_sets[guild.id].items():
            message_set_msg_ids = [message.id for message in message_set]
            if reaction_message.id in message_set_msg_ids:
                reaction_rfr_id = rfr_id
                break

        if reaction_rfr_id is None:
            return

        role_id = await self.get_reaction_role_id(payload.emoji.id, guild, reaction_rfr_id)
        role_obj = guild.get_role(int(role_id))

        await guild.get_member(payload.user_id).remove_roles(role_obj)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.member.bot:
            return

        rfr_emoji_list = await self.__gather_rfr_emoji(payload.member.guild)
        if str(payload.emoji.id) not in rfr_emoji_list:
            return

        self.logger.debug(payload)
        reaction_rfr_id = None
        reaction_channel = payload.member.guild.get_channel(int(payload.channel_id))
        reaction_message = await reaction_channel.fetch_message(int(payload.message_id))
        for rfr_id, message_set in self.rfr_message_sets[payload.member.guild.id].items():
            message_set_msg_ids = [message.id for message in message_set]
            if reaction_message.id in message_set_msg_ids:
                reaction_rfr_id = rfr_id
                break

        if reaction_rfr_id is None:
            return

        role_id = await self.get_reaction_role_id(payload.emoji.id, payload.member.guild, reaction_rfr_id)

        self.logger.debug(role_id)

        role_to_add_obj = payload.member.guild.get_role(int(role_id))
        await payload.member.add_roles(role_to_add_obj, reason="RFR Assignment")

    async def get_reaction_role_id(self, emoji_id, guild, rfr_id):
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", self.guild_db_name(guild))
                await cursor.execute("""
                    SELECT role_id
                    FROM r_for_r_emoji
                    INNER JOIN r_for_r_emoji_to_message
                    ON r_for_r_emoji.role_emoji_relation_id = r_for_r_emoji_to_message.role_emoji_relation_id
                    AND r_for_r_emoji_to_message.rfr_message_id = %s
                    WHERE emoji_id = %s
                """, (rfr_id, emoji_id,))
                return (await cursor.fetchone())[0]

    @commands.Cog.listener()
    async def on_ready(self):
        self.db_conn_cog = self.bot.get_cog("DBConnPool")
        await asyncio.sleep(0.5)
        for guild in self.bot.guilds:
            valid_rfr = await self.__get_valid_rfr_ids(guild)
            self.rfr_message_sets[guild.id] = {}
            try:
                await self.__rfr_infer_id_internal(guild, valid_rfr[0], None)
            except IndexError:
                pass
            await self.__update_rfr_message_list(valid_rfr, guild)
        self.logger.debug(self.rfr_message_sets)

    async def __update_rfr_message_list(self, rfr_id_list, guild):
        for rfr_id in rfr_id_list:
            self.rfr_message_sets[guild.id][rfr_id] = await self.__collect_rfr_messages(rfr_id, None, guild)

    async def __get_valid_rfr_ids(self, guild: discord.Guild):
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", self.guild_db_name(guild))
                await cursor.execute("""
                SELECT rfr_message_id
                FROM r_for_r_messages
                WHERE channel_message_id is not NULL
                """)
                valid_rfr = await cursor.fetchall()
                if valid_rfr is None:
                    await cursor.execute("""
                        SELECT rfr_message_id
                        FROM r_for_r_messages
                    """)
                    return await cursor.fetchall()
                else:
                    return [rfr_id[0] for rfr_id in valid_rfr]

    @staticmethod
    def get_rfr_table_name(rfr_id):
        return f"rfr_emoji_{str(rfr_id)}"

    async def __gather_rfr_emoji(self, guild: discord.Guild) -> List[discord.Emoji]:
        guild_db_name = self.guild_db_name(guild)
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    SELECT emoji_id
                    FROM r_for_r_emoji
                """)
                result_list = await cursor.fetchall()
        emoji_return_list = []
        for result in result_list:
            emoji_return_list.append(result[0])

        return emoji_return_list

    async def cog_check(self, ctx):
        """This method is a cog wide check to ensure users have "admin" roles,

        It will be called without the need for check decorators on every command.
        """
        for role in ctx.message.author.roles:
            if role.id in self.bot.bot_config["admin_role_id_list"]:
                # await self.rfr_infer_id(ctx, 0)
                return True
        return False

    async def __check_if_rfr_id_exists(self, guild_db_name, rfr_msg_id: int):
        """Function to check whether a given rfr_emoji table exists or not.

        :param guild_db_name: Name of guild database
        :param rfr_msg_id: ID of rfr message table to search for

        :returns: Boolean, whether or not table exists
        :rtype: bool"""
        async with self.db_conn_cog.conn_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("USE `%s`;", guild_db_name)
                await cursor.execute("""
                    SELECT count(rfr_message_id)
                    FROM r_for_r_messages
                    WHERE rfr_message_id = %s;
                """, (rfr_msg_id,))
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

    @staticmethod
    def guild_db_name(guild_data: Union[discord.Guild, str]):
        try:
            guild_id = str(guild_data.id)
        except AttributeError:
            guild_id = guild_data
        return f"guild_"+guild_id

    @commands.command()
    @commands.guild_only()
    async def rfr_infer_id(self, ctx, id_to_infer: str):
        """Guild only command to set the inferred rfr_key for this guild."""
        # self.logger.debug(id_to_infer)
        return await self.__rfr_infer_id_internal(ctx.guild, int(id_to_infer), ctx)

    async def __rfr_infer_id_internal(self, guild, id_to_infer, ctx: Optional[commands.Context]):
        if not await self.__check_if_rfr_id_exists(self.guild_db_name(guild), int(id_to_infer)):
            if ctx is not None:
                await ctx.send("Invalid id supplied.")
            return None
        if ctx is not None:
            self.inferred_rfr_ids[ctx.guild.id] = int(id_to_infer)
        else:
            self.inferred_rfr_ids[guild.id] = int(id_to_infer)
        # self.logger.debug(str(self.inferred_rfr_ids[ctx.guild.id]))
        return int(id_to_infer)

    @rfr_message_group.command(name="test")
    @commands.guild_only()
    async def rfr_message_test(self, ctx, rfr_rel_id: Optional[int], target_channel: Optional[
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
        guild_db_name = self.guild_db_name(ctx.guild)
        if target_channel is None:
            target_channel = ctx.channel

        if rfr_rel_id is None:
            rfr_rel_id = self.inferred_rfr_ids[ctx.guild.id]
        else:
            await self.rfr_infer_id(ctx, str(rfr_rel_id))

        if not await self.__check_if_rfr_id_exists(guild_db_name, rfr_rel_id):
            await ctx.send("You have supplied an invalid rfr identifier, please try "
                           "again", delete_after=self.bot.delete_msg_after)
            return

        await self.__create_rfr(ctx, rfr_rel_id, guild_db_name, target_channel, ctx.guild)

    async def __collect_rfr_messages(self, rfr_id, ctx: Optional[commands.Context], guild: Optional[discord.Guild]):
        """Collects and returns a list of rfr embed messages.

        :param ctx: Command context
        :param rfr_id: RFR identifier

        :returns: List of discord.Message or None if no messages found.
        :rtype: Union[List[discord.Message], NoneType]"""
        guild = ctx.guild if ctx is not None else guild
        if guild is None:
            return
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", self.guild_db_name(guild))
                await cursor.execute("""
                    SELECT channel_message_id
                    FROM r_for_r_messages
                    WHERE rfr_message_id = %s
                """, (rfr_id,))
                query_result = await cursor.fetchone()
                self.logger.debug(f"Query result: {query_result}")
                lead_rfr_message_id = query_result[0]
                if lead_rfr_message_id is None:
                    # await ctx.send("Supplied rfr ID invalid.",
                    #                delete_after=self.bot.delete_msg_after)
                    return
        # Now we have a valid rfr_id and possibly valid message ID that should point to the first
        # message in the chain of RFR messages.
        rfr_message_list = []
        rfr_channel = None
        if ctx is not None:
            rfr_message = await commands.MessageConverter().convert(ctx, lead_rfr_message_id)
        else:
            rfr_channel = guild.get_channel(int(lead_rfr_message_id.split("-")[0]))
            rfr_message = await rfr_channel.fetch_message(lead_rfr_message_id.split("-")[1])
        if rfr_message is None:
            await ctx.send("Contained rfr leading message ID invalid.")
        rfr_message_embed = rfr_message.embeds[0]
        rfr_message_list.append(rfr_message)
        embed_footer = rfr_message_embed.footer.text
        while "RFR-End" not in embed_footer:
            if ctx is not None:
                rfr_message = await commands.MessageConverter().convert(ctx, embed_footer.split("_")[1])
            else:
                rfr_message = await rfr_channel.fetch_message(embed_footer.split("-")[1])
            rfr_message_embed = rfr_message.embeds[0]
            embed_footer = rfr_message_embed.footer.text
            rfr_message_list.append(rfr_message)
        self.logger.debug(rfr_message_list)
        return rfr_message_list

    async def __update_rfr_embeds(self, ctx, rfr_id: Optional[int]):
        guild_db_name = self.guild_db_name(ctx.guild)
        if rfr_id is None:
            rfr_id = self.inferred_rfr_ids[ctx.guild.id]
        else:
            rfr_id = await self.rfr_infer_id(ctx, str(rfr_id))
            if rfr_id is None:
                return
        rfr_message_list = await self.__collect_rfr_messages(ctx, rfr_id, None)
        if rfr_message_list is None:
            # await ctx.send(f"There is no RFR message associated with the RFR ID: `{rfr_id}` "
            #                f"`{self.cmd_prefix}rfr message send [rfr_id] <channel_id>`")
            return

        rfr_channel = rfr_message_list[0].channel
        rfr_relations = await self.__get_all_rfr_relations(rfr_id, guild_db_name)
        rfr_restruct = {
            "role_ids": [relation[0] for relation in rfr_relations],
            "emoji_ids": [relation[1] for relation in rfr_relations],
            "names": [relation[2] for relation in rfr_relations]
        }

        # Now iterate through all existing embeds and search for rfr's that already exist,
        # plop them into a data structure to hold this information and find gaps where we can
        # place any new rfr's, if it isn't meant to be here, clear if off the message
        # rfr_emoji_by_message = []
        embed_msg_list = []
        for rfr_message in rfr_message_list:
            loc_message_current_valid_rfr_emoji = []
            embed_emoji_to_clear = []
            rfr_message_embed = rfr_message.embeds[0]
            for reaction in rfr_message.reactions:
                if str(reaction.emoji.id) in rfr_restruct["emoji_ids"]:
                    loc_message_current_valid_rfr_emoji.append(reaction.emoji.id)
                    found_emoji_index = rfr_restruct["emoji_ids"].index(str(reaction.emoji.id))
                    for list_item in rfr_restruct.values():
                        del list_item[found_emoji_index]
                else:
                    embed_emoji_to_clear.append(reaction.emoji)
                    await reaction.clear()
                    self.logger.debug(f"Removed reaction {reaction.emoji}")
            embed_field_list = rfr_message_embed.fields
            for i in range(len(embed_field_list)):
                for emoji_to_remove in embed_emoji_to_clear:
                    emoji_string = re.search(r'<:\w*:\d*>', embed_field_list[i].value)
                    if emoji_string is not None:
                        emoji_in_field = await commands.EmojiConverter().convert(ctx, emoji_string[0])
                        if emoji_to_remove.id == emoji_in_field.id:
                            rfr_message_embed.remove_field(i)
                            self.logger.debug(f"Removed field {emoji_to_remove}"
                                              f"with index {i}")
            # Append the cleaned embed, we're not going to cascade emoji to the lowest embed as
            # that would delete a large number of reactions that really don't need to be deleted
            # from the rfr messages.
            embed_msg_list.append((rfr_message_embed, rfr_message))

        # Now we've cleaned the message of emoji that aren't meant to be attached,
        # let's start adding the new emoji in any empty slots.
        for embed_message_index in range(len(embed_msg_list)):
            embed = embed_msg_list[embed_message_index][0]
            emoji_indicies_added = []
            for emoji_to_add_index in range(len(rfr_restruct["emoji_ids"])):
                if len(embed.fields) == 18:
                    self.logger.debug("Embed full, moving on to next")
                    if embed_message_index == len(embed_msg_list) - 1 and len(rfr_restruct["emoji_ids"]) > 0:
                        num_fields_required = len(rfr_restruct["emoji_ids"])
                        num_embeds_required = math.ceil(num_fields_required / 18)
                        placeholder_messages = [await rfr_channel.send("RFR Placeholder") for _ in
                                                range(num_embeds_required)]
                        embed_msg_list[-1][0].set_footer(
                            text=f"{rfr_id}_{placeholder_messages[0].channel.id}-{placeholder_messages[0].id}")
                        await embed_msg_list[-1][1].edit(content=None, embed=embed_msg_list[-1][0])
                        for i in range(len(placeholder_messages)):
                            role_emoji_name_tuple_list = []
                            for j in range(len(rfr_restruct["emoji_ids"])):
                                if j == 18:
                                    break
                                role = rfr_restruct["role_ids"][j]
                                emoji = rfr_restruct["emoji_ids"][j]
                                name = rfr_restruct["names"][j]
                                role_emoji_name_tuple_list.append((role, emoji, name))
                            for index in range(len(role_emoji_name_tuple_list)):
                                del rfr_restruct["role_ids"][index]
                                del rfr_restruct["emoji_ids"][index]
                                del rfr_restruct["names"][index]
                            if i == len(placeholder_messages) - 1:
                                new_embed = await self.__create_new_rfr_embed(ctx, placeholder_messages[i], None,
                                                                              rfr_id, role_emoji_name_tuple_list)
                            else:
                                new_embed = await self.__create_new_rfr_embed(ctx, placeholder_messages[i],
                                                                              placeholder_messages[i+1], rfr_id,
                                                                              role_emoji_name_tuple_list)
                            embed_msg_list.append((new_embed, placeholder_messages[i]))
                    break
                curr_emoji = await commands.EmojiConverter().convert(ctx, rfr_restruct["emoji_ids"][emoji_to_add_index])
                curr_role = await commands.RoleConverter().convert(ctx, rfr_restruct["role_ids"][emoji_to_add_index])
                curr_rfr_name = (
                    curr_role.name if rfr_restruct["names"][emoji_to_add_index] is None
                    else rfr_restruct["names"][emoji_to_add_index]
                )
                embed.add_field(name=curr_rfr_name,
                                value=f"{curr_emoji} "
                                      f"{curr_role.mention}",
                                inline=True)
                emoji_indicies_added.append(emoji_to_add_index)
                await embed_msg_list[embed_message_index][1].add_reaction(curr_emoji)

            for emoji_index in emoji_indicies_added:
                for value_list in rfr_restruct.values():
                    del value_list[emoji_index]

        # Now we have edited and created where required, time to push these changes onto the
        # placeholder messages.
        for embed, message in embed_msg_list:
            await message.edit(content=None, embed=embed)

        await self.__update_rfr_message_list(rfr_id, ctx.guild)

        return embed_msg_list

    @staticmethod
    async def __create_new_rfr_embed(
            ctx: commands.Context, curr_message: discord.Message, next_message: Union[discord.Message, None],
            rfr_id: int, role_emoji_rfrname_list: List[Tuple[str, str, Union[str, None]]]
    ) -> Union[discord.Embed, None]:
        new_embed = discord.Embed(title=f"SVGEBot RfR",
                                  description="React for Role Interface",
                                  colour=0x6b2b2b)
        if len(role_emoji_rfrname_list) > 18:
            return None

        for role, emoji, name in role_emoji_rfrname_list:
            emoji_obj = await commands.EmojiConverter().convert(ctx, emoji)
            role_obj = await commands.RoleConverter().convert(ctx, role)
            if name is None:
                name = role_obj.name
            new_embed.add_field(name=name,
                                value=f"{emoji_obj} {role_obj.mention}",
                                inline=True)
            await curr_message.add_reaction(emoji_obj)

        if next_message is None:
            new_embed.set_footer(text=f"{rfr_id}_RFR-End")
        else:
            new_embed.set_footer(text=f"{rfr_id}_{next_message.channel.id}-{next_message.id}")

        return new_embed

    @commands.guild_only()
    @rfr_message_group.command(name="send")
    async def rfr_final_message_send(self, ctx, rfr_id: Optional[str],
                                     channel: Optional[discord.TextChannel]):
        if channel is None:
            channel = ctx.channel

        if rfr_id is None:
            rfr_id = self.inferred_rfr_ids[ctx.guild.id]
        else:
            rfr_id = await self.rfr_infer_id(ctx, str(rfr_id))
            if rfr_id is None:
                return

        guild_db_name = self.guild_db_name(ctx.guild)

        rfr_return = await self.__create_rfr(ctx, rfr_id, guild_db_name, channel,
                                             ctx.guild)
        rfr_list, channel_message_id_list = rfr_return

        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    UPDATE r_for_r_messages
                    SET channel_message_id = %s
                    WHERE rfr_message_id = %s
                """, (channel_message_id_list[0], rfr_id,))

    async def __create_rfr(self, ctx, rfr_id, guild_db_name, target_channel: discord.TextChannel,
                           guild: discord.Guild):
        role_emoji_name_tuple_list = await self.__get_all_rfr_relations(rfr_id, guild_db_name)
        # Because each message can only support up to a maximum of 20 reactions, we must split
        # the rfr into separate embeds and messages as required. To make things look nice, we're
        # going to cap out at 18
        num_fields_required = len(role_emoji_name_tuple_list)
        num_embeds_required = math.ceil(num_fields_required / 18)
        rfr_list_offset = 0
        embed_list = []  # type: List
        for i in range(num_embeds_required):
            embed_list.append((await target_channel.send("RFR Placeholder"), None))

        channel_message_id_list = []

        rfrs_to_add = []
        start_index_offset = 0
        for i in range(len(embed_list)):
            rfrs_to_add.append([])
            for j in range(start_index_offset, len(role_emoji_name_tuple_list)):
                if j == 18 + start_index_offset:
                    start_index_offset += j
                    break
                rfrs_to_add[i].append(role_emoji_name_tuple_list[j])

        for i in range(len(embed_list)):
            curr_message_obj = embed_list[i][0]
            if i != len(embed_list) - 1:
                next_message = embed_list[i+1][0]
            else:
                next_message = None
            new_embed = await self.__create_new_rfr_embed(ctx, curr_message_obj, next_message, rfr_id,
                                                          rfrs_to_add[i])

            await curr_message_obj.edit(content=None, embed=new_embed)
            embed_list[i] = (curr_message_obj, new_embed)
            channel_message_id_list.append(f"{curr_message_obj.channel.id}-{curr_message_obj.id}")
        return embed_list, channel_message_id_list

    async def __get_all_rfr_relations(self, rfr_id: int, guild_db_name):
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
                    AND r_for_r_emoji_to_message.rfr_message_id = %s
                """, rfr_id)
                return await cursor.fetchall()

    @rfr_message_group.command(name="create")
    @commands.guild_only()
    async def rfr_message_create(self, ctx):
        """Create the backend structures required for a new rfr message set"""
        guild_db_name = self.guild_db_name(ctx.guild)
        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    INSERT INTO r_for_r_messages (rfr_message_id, channel_message_id) 
                    VALUES (NULL, NULL);
                """)
                await cursor.execute("SELECT LAST_INSERT_ID();")
                current_rfr_id = (await cursor.fetchone())[0]
        await self.rfr_infer_id(ctx, current_rfr_id)
        await ctx.send(f"Created rfr with id: `{str(current_rfr_id)}`. "
                       f"Do not lose this identifier.")

    async def __create_new_rfr_relation(self, emoji_id, role_id, rfr_name, ctx):
        """Create a new rfr record.

        :param emoji_id: Emoji ID
        :param role_id: Role ID
        :param rfr_name: Name of rfr relation
        :param ctx: Command context

        :return: rfr emoji role relation_id or None.
        :rtype: Union[int, NoneType]"""
        guild_db_name = self.guild_db_name(ctx.guild)
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
                    (DEFAULT, %(role_id)s, %(emoji_id)s, %(rfr_name)s)
                    ON DUPLICATE KEY UPDATE role_emoji_relation_id=role_emoji_relation_id;
                """, {"role_id": role_id, "emoji_id": emoji_id, "rfr_name": rfr_name})
                await cursor.execute("SELECT LAST_INSERT_ID()")
                return (await cursor.fetchone())[0]

    @rfr_message_group.command(name="add")
    @commands.guild_only()
    async def rfr_message_add_rfr(self, ctx, rfr_id: Optional[int], emoji: Union[
            discord.Emoji, str], role: discord.Role, *, rfr_name: Optional[str]):
        """Add either a pre-existing rfr relation or a new rfr relation
        to a preexisting rfr message.

        :param ctx: Auto-filled by library
        :param rfr_id: ID of rfr message to add an emoji to
        :param emoji: Emoji to add to rfr
        :param role: Role to add to rfr
        :param rfr_name: Name of rfr relation
        """
        guild_db_name = self.guild_db_name(ctx.guild)
        if type(emoji) is str:
            emoji = await commands.EmojiConverter().convert(ctx, emoji.strip(":"))

        if rfr_id is None:
            rfr_id = self.inferred_rfr_ids[ctx.guild.id]
            # self.logger.debug(rfr_msg_id)
        else:
            rfr_id = await self.rfr_infer_id(ctx, str(rfr_id))
            if rfr_id is None:
                return

        role_emoji_relation_id = await self.__create_new_rfr_relation(emoji.id, role.id,
                                                                      rfr_name, ctx)
        # Check whether or not the addressed rfr message exists
        if not bool(await self.__check_if_rfr_id_exists(guild_db_name, rfr_id)):
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
                """, (int(rfr_id), int(role_emoji_relation_id),))
        await ctx.send(f'Added emoji: "{emoji}" to rfr '
                       f'message with ID: `{rfr_id}`. To test how this message will look, '
                       f'send: `>>rfr message test {rfr_id}` in a private channel.',
                       delete_after=self.bot.delete_msg_after)

    @rfr_message_group.command(name="remove", aliases=["delete"])
    @commands.guild_only()
    async def rfr_message_remove_rfr(self, ctx, role: discord.Role):

        guild_db_name = self.guild_db_name(ctx.guild)

        # if type(emoji) is str:
        #     # Handles external emoji when user lacks nitro
        #     emoji_or_role = await commands.EmojiConverter().convert(ctx, emoji_or_role.strip(":"))

        async with self.db_conn_cog.conn_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("USE `%s`", guild_db_name)
                await cursor.execute("""
                    DELETE FROM r_for_r_emoji
                    WHERE role_id = %s
                """, (str(role.id),))
                # if isinstance(emoji_or_role, discord.Emoji):
                #     await cursor.execute("""
                #         DELETE FROM r_for_r_emoji
                #         WHERE emoji_id = %s
                #     """, (str(emoji_or_role.id),))
                # elif isinstance(emoji_or_role, discord.Role):
                #     await cursor.execute("""
                #         DELETE FROM r_for_r_emoji
                #         WHERE role_id = %s
                #     """, (str(emoji_or_role.id),))
                # else:
                #     await ctx.send("Invalid role or emoji supplied.", delete_after=self.bot.delete_msg_after)

    # Currently doesn't work for some reason
    @react_for_role_group.command(name="add")
    @commands.guild_only()
    async def rfr_emoji_add_shortcut(self, ctx, rfr_id: Optional[int], emoji: Union[
            discord.Emoji, str], role: discord.Role, *, rfr_name: Optional[str]):
        """Shortcut command to add an emoji to a RFR, """
        await ctx.invoke(rfr_id, emoji, role, rfr_name)


def setup(bot):
    bot.add_cog(ReactForRole(bot))
