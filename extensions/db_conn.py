import asyncio
import aiomysql
import logging
import warnings
import json
import os
import datetime
from copy import deepcopy
from shutil import copyfile
from discord.ext import commands, tasks


class DBConnPool(commands.Cog):
    """Cog that offers aioMySQL connections from an internally managed
    pool. This is a dependency cog and should not contain any commands
    for the end user.

    Ideally please use the "with... as..." implementation of self.acquire()
    as this will automatically release the connection once you are done using it,
    saving time and space from the calamity of all ten connections in the pool being
    in active use.
    """
    def __init__(self, bot):
        self.bot = bot
        self.event_loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("SVGEBot.DBConn")
        self.cog_config = None
        self.__guild_db_list = []
        self.__open_connection_list = {}
        self.__get_config()
        self.conn_pool = None
        self.logger.info("Loaded DBConnPool")

    @property
    def guild_db_list(self):
        return self.__guild_db_list

    async def shutdown(self):
        self.conn_pool.terminate()
        self.logger.info("Database connection pool closing")
        await self.conn_pool.wait_closed()
        self.logger.info("Database connection pool closed")

    def cog_unload(self):
        self.logger.info("Unloaded DBConnPool")

    def __get_config(self, run_counter=0):
        cog_conf_location = "./extensions/extension_configs/db_conn_config.json"
        default_cog_conf_loc = "./extensions/extension_configs/db_conn_config_default.json"
        if os.path.exists(cog_conf_location):
            with open(cog_conf_location) as cog_config_obj:
                self.cog_config = json.load(cog_config_obj)
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

    async def __create_guild_member_tables(self):
        async with self.conn_pool.acquire() as db_connection:
            async with db_connection.cursor() as db_cursor:
                create_guild_member_table_query = """
                CREATE TABLE IF NOT EXISTS guild_members (
                    discord_user_id INT NOT NULL,
                    discord_username VARCHAR(37),
                    memberships TEXT,
                    verified BOOLEAN,
                    PRIMARY KEY ( discord_user_id )
                )"""
                create_guild_verification_table_query = """
                CREATE TABLE IF NOT EXISTS member_verification (
                    discord_user_id INT NOT NULL,
                    email VARCHAR(320),
                    verification_key CHAR(10),
                    last_verification_req DATETIME,
                    PRIMARY KEY ( discord_user_id )
                )
                """
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    for guild_db_name in self.guild_db_list:
                        await db_cursor.execute("""USE `%s`""", guild_db_name)
                        await db_cursor.execute(create_guild_member_table_query)
                        await db_cursor.execute(create_guild_verification_table_query)

    async def __create_guild_databases(self, guild_list):
        """This coroutine will create databases for guilds with regular
        names, if and only if they do not already exist, the first query
        searches for databases of names corresponding to their guild,
        then databases will be made for all queries that return nothing.
        """
        guild_table_names = []
        for guild in guild_list:
            guild_table_names.append("guild_"+str(guild.id))
        async with self.conn_pool.acquire() as db_connection:
            async with db_connection.cursor() as db_cursor:
                tab_search_query = """SELECT SCHEMA_NAME
                    FROM information_schema.SCHEMATA
                    WHERE SCHEMA_NAME = %s"""
                await db_cursor.execute(
                    tab_search_query,
                    "all_user_db"
                )

                await db_cursor.executemany(
                    tab_search_query,
                    guild_table_names
                )
                db_check_results = await db_cursor.fetchmany()
                db_to_create = []
                for i in range(len(db_check_results)):
                    db_to_create.append(guild_table_names[i])
                await db_cursor.executemany(
                    """CREATE DATABASE `%s`""",
                    db_to_create
                )
        seen_guilds = set(self.guild_db_list)
        for guild_db_name in guild_table_names:
            if guild_db_name not in seen_guilds:
                self.__guild_db_list.append(guild_table_names)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.__acquire_db_pool()
        await self.__create_guild_databases(self.bot.guilds)
        await self.__create_guild_member_tables()

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self.logger.debug(f"Joined guild {guild.name} ({guild.id})")
        await self.__create_guild_databases([guild])
        await self.__create_guild_member_tables()

    # @tasks.loop(seconds=30)
    # async def __ping_all_connections(self):
    #     """Ongoing task that slims down the number of connection objects within
    #     this pool, all free connections will be removed, every three minutes,
    #     all inactive connections that are not free (unused for over 15 seconds)
    #     will be released and made "free" connections, all connections that have
    #     been used minimally in the last three minutes will be pinged and have a
    #     reconnect attempt made."""
    #     # Close all "free" connections
    #     await self.__local_conn_pool.clear()
    #     self.logger.debug("Connection maintenance loop.")
    #     # Iterate and release, or ping all "in-use" connections
    #     for connection, old_last_used in self.__open_connection_list.items():
    #         if connection.last_used - old_last_used > 15:
    #             # We're going to release connections that have been unused for
    #             # over 15 seconds.
    #             self.__local_conn_pool.release(connection)
    #             del self.__open_connection_list[connection]
    #             self.logger.info("Released connection due to inactivity")
    #         else:
    #             await connection.ping(reconnect=True)
    #             self.__open_connection_list[connection] = deepcopy(connection.last_usage)

    async def __acquire_db_pool(self):
        if self.conn_pool is None:
            try:
                self.conn_pool = await aiomysql.create_pool(
                    **self.cog_config["_db"]
                )
                self.logger.info("Database connection pool acquired")
            except BaseException as connection_exception:
                self.logger.exception(f"Exception when connecting to database:"
                                      f"\n{connection_exception}\n\n "
                                      f"check your configuration and try again.")
                input()
                exit(1)


def setup(bot):
    bot.add_cog(DBConnPool(bot))
