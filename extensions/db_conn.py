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

    Ideally please use the "with... as..." implementation of self.conn_pool.acquire()
    as this will automatically release the connection once you are done using it,
    saving time and space from the calamity of all ten connections in the pool being
    in active use.
    """
    def __init__(self, bot):
        self.bot = bot
        self.event_loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("SVGEBot.DBConn")
        self.cog_config = None
        self.__guild_dbs_ready = False
        self.__guild_db_list = []
        self.__open_connection_list = {}
        self.__get_config()
        self.conn_pool = None
        self.logger.info("Loaded DBConnPool")

    @property
    def guild_dbs_ready(self):
        return self.__guild_dbs_ready

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

    @staticmethod
    def __gen_bidirectional_aliases(raw_config):
        """Quick and dirty method to create a reversed guild alias
        dictionary."""
        raw_config["guild_aliases_reversed"] = {}
        for key, value in raw_config["guild_aliases"].items():
            raw_config["guild_aliases_reversed"][value] = key
        return raw_config

    def __get_config(self, run_counter=0):
        cog_conf_location = "./extensions/extension_configs/db_conn_config.json"
        default_cog_conf_loc = "./extensions/extension_configs/db_conn_config_default.json"
        if os.path.exists(cog_conf_location):
            with open(cog_conf_location) as cog_config_obj:
                self.cog_config = self.__gen_bidirectional_aliases(json.load(cog_config_obj))
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

    async def __create_guild_member_tables(self, guild_name):
        async with self.conn_pool.acquire() as db_connection:
            async with db_connection.cursor() as db_cursor:
                create_guild_member_table_query = """
                CREATE TABLE IF NOT EXISTS guild_members (
                    discord_user_id CHAR(18) NOT NULL,
                    discord_username VARCHAR(37),
                    memberships TEXT,
                    verified BOOLEAN,
                    modifiers TEXT,
                    PRIMARY KEY ( discord_user_id )
                )"""
                create_guild_verification_table_query = """
                CREATE TABLE IF NOT EXISTS member_verification (
                    discord_user_id CHAR(18) NOT NULL,
                    email VARCHAR(320) UNIQUE,
                    verification_key VARCHAR(64),
                    last_verification_req DATETIME,
                    CONSTRAINT `fk_user_id`
                    FOREIGN KEY ( discord_user_id )
                    REFERENCES guild_members ( discord_user_id )
                    ON UPDATE CASCADE ON DELETE CASCADE,
                    PRIMARY KEY ( discord_user_id )
                )
                """
                react_for_role_table_query = """
                CREATE TABLE IF NOT EXISTS r_for_r_emoji (
                    role_emoji_relation_id INT AUTO_INCREMENT,
                    role_id CHAR(18) NOT NULL,
                    emoji_id CHAR(18) NOT NULL,
                    name VARCHAR(64),
                    PRIMARY KEY ( 
                        role_emoji_relation_id,
                        role_id,
                        emoji_id,
                        name 
                    )
                );
                CREATE TABLE IF NOT EXISTS r_for_r_messages (
                    rfr_message_id INT AUTO_INCREMENT,
                    channel_message_id CHAR(37) UNIQUE,
                    PRIMARY KEY ( rfr_message_id )
                ); 
                CREATE TABLE IF NOT EXISTS r_for_r_emoji_to_message (
                    rfr_message_id INT,
                    role_emoji_relation_id INT,
                    CONSTRAINT `fk_rfr_id`
                    FOREIGN KEY ( rfr_message_id ) 
                    REFERENCES r_for_r_messages ( rfr_message_id )
                    ON DELETE CASCADE,
                    CONSTRAINT `fk_emoji_role_id` 
                    FOREIGN KEY ( role_emoji_relation_id )
                    REFERENCES r_for_r_emoji ( role_emoji_relation_id )
                    ON DELETE CASCADE,
                    PRIMARY KEY (rfr_message_id, role_emoji_relation_id)
                )"""
                warning_table_query = """
                CREATE TABLE IF NOT EXISTS warning_table (
                    warning_id INT AUTO_INCREMENT,
                    expired BOOLEAN,
                    warned_user_id CHAR(18),
                    admin_user_id CHAR(18),
                    recent_user_messages MEDIUMTEXT,
                    reason TEXT NOT NULL,
                    datetime DATETIME,
                    CONSTRAINT `fk_warned_user_id`
                    FOREIGN KEY ( warned_user_id )
                    REFERENCES guild_members ( discord_user_id )
                    ON UPDATE CASCADE ON DELETE SET NULL,
                    CONSTRAINT `fk_admin_user_id`
                    FOREIGN KEY ( admin_user_id )
                    REFERENCES guild_members ( discord_user_id )
                    ON UPDATE CASCADE ON DELETE SET NULL,
                    PRIMARY KEY ( warning_id )
                )
                """
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    await db_cursor.execute("""USE `%s`""", guild_name)
                    await db_cursor.execute(create_guild_member_table_query)
                    await db_cursor.execute(create_guild_verification_table_query)
                    await db_cursor.execute(react_for_role_table_query)
                    await db_cursor.execute(warning_table_query)

    async def __create_guild_databases(self, guild_list):
        """This coroutine will create databases for guilds with regular
        names, if and only if they do not already exist, the first query
        searches for databases of names corresponding to their guild,
        then databases will be made for all queries that return nothing.
        """
        guild_database_names = []
        for guild in guild_list:
            guild_database_names.append("guild_"+str(guild.id))
        async with self.conn_pool.acquire() as db_connection:
            async with db_connection.cursor() as db_cursor:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    tab_search_query = """SELECT SCHEMA_NAME
                        FROM information_schema.SCHEMATA
                        WHERE SCHEMA_NAME = %s;"""
                    '''await db_cursor.execute(
                        tab_search_query,
                        "'general_bot_db'"
                    )'''
                    await db_cursor.execute(
                        """CREATE DATABASE IF NOT EXISTS `%(db_name)s`;
                        USE `%(db_name)s`;
                        CREATE TABLE IF NOT EXISTS user_tracking_table (
                            discord_user_id VARCHAR(18) NOT NULL,
                            PRIMARY KEY ( discord_user_id )
                        )""",
                        {"db_name": "general_bot_db"}
                    )

                    await db_cursor.executemany(
                        tab_search_query,
                        guild_database_names
                    )
                    db_check_results = await db_cursor.fetchmany()
                    db_to_create = []
                    for i in range(len(db_check_results)):
                        db_to_create.append(guild_database_names[i])
                    await db_cursor.executemany(
                        """CREATE DATABASE IF NOT EXISTS `%s`""",
                        guild_database_names
                    )
        seen_guilds = set(self.guild_db_list)
        for guild_db_name in guild_database_names:
            if guild_db_name not in seen_guilds:
                await self.__create_guild_member_tables(guild_db_name)
                self.__guild_db_list.append(guild_database_names)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.__acquire_db_pool()
        if not self.guild_dbs_ready:
            await self.__create_guild_databases(self.bot.guilds)
            self.__guild_dbs_ready = True

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self.logger.debug(f"Joined guild {guild.name} ({guild.id})")
        await self.__create_guild_databases([guild])

    async def __acquire_db_pool(self):
        if self.conn_pool is None:
            try:
                self.conn_pool = await aiomysql.create_pool(
                    **self.cog_config["_db"]
                )
                self.logger.info("Database connection pool acquired")
                async with self.conn_pool.acquire() as encoding_fix_conn:
                    async with encoding_fix_conn.cursor() as encoding_fix_cursor:
                        await encoding_fix_cursor.execute(
                            """SET GLOBAL character_set_server = 'utf8mb4';
                            SET GLOBAL collation_server = 'utf8_general_ci';
                            SET GLOBAL init_connect='utf8mb4'"""
                        )
            except BaseException as connection_exception:
                self.logger.exception(f"Exception when connecting to database:"
                                      f"\n{connection_exception}\n\n "
                                      f"check your configuration and try again.")
                input()
                exit(1)


def setup(bot):
    bot.add_cog(DBConnPool(bot))
