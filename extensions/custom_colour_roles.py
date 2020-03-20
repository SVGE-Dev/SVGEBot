import discord
import logging
import asyncio
import math
import json

from discord.ext import commands, tasks


class ColourMeCog(commands.Cog, name="Custom Colours"):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("CustomColours")
        self.delete_message_after = self.bot.bot_config["delete_msg_after"]
        self.cog_config = self._get_cog_config
        self.logger.info("Loaded CustomColours")

    @commands.Cog.listener()
    async def on_ready(self):
        pass

    @tasks.loop(seconds=60.0)
    async def colour_role_check_loop(self):
        for guild in self.bot.guilds:
            await self._clean_colour_roles(guild)

    @property
    def _get_cog_config(self):
        with open("./extensions/extension_configs/custom_colour_roles.json", "r") as cog_config:
            return json.load(cog_config)

    @commands.command(name="colourme refresh")
    @commands.has_permissions(administrator=True)
    async def refresh_config(self):
        self.cog_config = self._get_cog_config

    def cog_unload(self):
        self.logger.info("Unloaded CustomColours")

    async def _clean_colour_roles(self, context_guild):
        await asyncio.sleep(0.5)
        for crole in context_guild.roles:
            if "SVGE[0x" in crole.name:
                if not crole.members:
                    await crole.delete(reason="Automatic custom colour deletion when unused.")
        self.logger.info("Cleaned out empty colour roles")

    def _valid_colour_roles_string_gen(self, guild_id):
        basestring = ""
        for role_id in self.cog_config["colour_req_role_id_list"][str(guild_id)]:
            basestring += f"- {self.bot.get_role(role_id).name}\n"

        return basestring

    async def can_req_colour_check(self, ctx):
        try:
            guild_permitted_roles = self.cog_config["colour_req_role_id_list"][str(ctx.guild.id)]
        except KeyError:
            self.logger.info(f"Unable to find permitted colour roles for {ctx.guild.name} (ID: "
                             f"{ctx.guild.id}), assuming no role required for custom colour")
            return True
        for role in ctx.author.roles:
            if role.id in guild_permitted_roles:
                return True
        return False

    @commands.command(name="colourme")
    async def colour_me(self, ctx, colour_hex: str):
        """Gives the command invoker a custom colour role if they satisfy given conditions.
        If colour_hex is given as "remove", the bot will remove the colour role and exit the
        operation.
        """

        # Have to handle role validity internally due to discord.py not liking piecewise custom
        # checks
        user_can_req_colour = await self.can_req_colour_check(ctx)

        if not user_can_req_colour:
            await ctx.send(f"You do not have permissions required to request a custom colour, "
                           f"obtain one of the following roles:\n" +
                           self._valid_colour_roles_string_gen(ctx.guild.id),
                           delete_after=self.delete_message_after)

        # Preprocess the colour
        if colour_hex.lower() == "remove":
            for arole in ctx.author.roles:
                if "SVGE[0x" in arole.name:
                    await ctx.author.remove_roles(arole, reason="User requested colour role "
                                                                "removal.")

            await self._clean_colour_roles(ctx.guild)
            return

        if len(colour_hex) > 6:
            await ctx.send("The colour string requested is invalid.",
                           delete_after=self.delete_message_after)
            return
        colour_hex_split = [colour_hex[0:2], colour_hex[2:4], colour_hex[4:6]]
        colour_dec_split = []
        colour_dec = 0
        for colour in colour_hex_split:
            try:
                colour_dec = int(colour, 16)
            except ValueError:
                await ctx.send("Invalid colour input. If you have included #, "
                               "omit it and try again.")
                return
            if not (0 <= colour_dec <= 255):
                await ctx.message(f"The colour: {colour_hex[0:6]} sits outside of permitted "
                                  f"ranges.", delete_after=self.delete_message_after)
                return
            colour_dec_split.append(colour_dec)

        exclusion_cube_origins = []

        admin_role_obj_list = []
        for admin_role_id in self.bot.bot_config["admin_role_id_list"]:
            # Let's first gather all the admin roles
            admin_role = ctx.guild.get_role(admin_role_id)
            if admin_role is not None:
                # Now find its colour and add it to the list of exclusion origins
                admin_role_obj_list.append(admin_role)
            else:
                self.logger.debug("Admin role defined in config not found in guild.")

        # Set up exclusion zones for colours
        for admin_role in admin_role_obj_list:
            # Now find its colour and add it to the list of exclusion origins
            admin_role_colour = admin_role.colour.to_rgb()
            exclusion_cube_origins.append(list(admin_role_colour))

        for extra_exclusion_colour in self.cog_config["extra_exclusion_colours"][str(ctx.guild.id)]:
            hex_exclusion_colour_split = [extra_exclusion_colour[0:2],
                                          extra_exclusion_colour[2:4],
                                          extra_exclusion_colour[4:6]]
            exclusion_colour_dec = []
            for colour in hex_exclusion_colour_split:
                exclusion_colour_dec.append(int(colour, 16))
            exclusion_cube_origins.append(exclusion_colour_dec)

        # Now we have all of the required cube origins, time to check our colour against each.
        for cube_center in exclusion_cube_origins:
            in_cube = True
            for i in range(3):
                dim_min_max = [cube_center[i] - self.cog_config["exclusion_side_length"],
                               cube_center[i] + self.cog_config["exclusion_side_length"]]
                if not (dim_min_max[0] < colour_dec_split[i] < dim_min_max[1]):
                    in_cube = False
                    break
            if colour_dec == cube_center:
                in_cube = True
            if in_cube:
                await ctx.send(f"The colour you have selected is too close to that of an admin "
                               f"role or protected colour.\n\nYour colour (decimal): "
                               f"{colour_dec_split} was too close to {cube_center}. \nChange "
                               f"one or more of the components such that they are "
                               f"{math.ceil(self.cog_config['exclusion_side_length'] / 2)} away "
                               f"from the protected colour.")
                return

        # Not much left to do, only need to create the custom colour role and make sure that it
        # sits below the lowest defined admin role.
        admin_role_pos_list = {}
        for admin_role in admin_role_obj_list:
            admin_role_pos_list[admin_role.position] = admin_role

        sorted_admin_list_pos = sorted(admin_role_pos_list)

        # Now we have the sorted list of admin roles, let's query all roles and see if
        # we already have the requested colour created. SVGEBot colour roles have the naming
        # convention: SVGE[0x<R><G><B>] in hex.
        try:
            prev_colour = await commands.RoleConverter().convert(ctx, f"SVGE[0x{colour_hex.upper()}]")
            await prev_colour.edit(position=sorted_admin_list_pos[0])
            await ctx.author.add_roles(prev_colour, reason="Custom colour requested.")
            return
        except commands.BadArgument:
            # The role doesn't already exist, let's pass.
            pass

        # Now to create the role we wanted all along.
        new_colour_role = await ctx.guild.create_role(
            name=f"SVGE[0x{colour_hex.upper()}]",
            reason="Custom colour role generation by SVGEBot.",
            colour=discord.Colour.from_rgb(r=colour_dec_split[0],
                                           g=colour_dec_split[1],
                                           b=colour_dec_split[2]))

        await new_colour_role.edit(position=sorted_admin_list_pos[0])
        await new_colour_role.edit(position=sorted_admin_list_pos[0])

        for invoker_role in ctx.author.roles:
            if "SVGE[0x" in invoker_role.name:
                await ctx.author.remove_roles(invoker_role,
                                              reason="Removing old colour role from user.")

        await ctx.author.add_roles(new_colour_role,
                                   reason="Automatic custom colour allocation by request.")

        self.logger.debug(f"Assigned colour role: {new_colour_role.name} to "
                          f"{ctx.message.author.name}#{ctx.message.author.discriminator}.")

        await self._clean_colour_roles(ctx.guild)


def setup(bot):
    bot.add_cog(ColourMeCog(bot))
