import asyncio
import aiomysql
import logging
import json
import os
from copy import deepcopy
from shutil import copyfile
from discord.ext import commands, tasks


class DBConnPool(commands.Cog, name="Database Connection Pool"):
    """Cog that offers aioMySQL connections from an internally managed
    pool. This is a dependency cog and should not contain any commands
    for the end user.

    If your cog needs to use the common database pool, do the following:
        1) Establish a database connection from the pool by addressing:
            the acquire_db_connection() coroutine, which will return your
            connection object.
        2) Ensure your usage of said connection object is somewhat active,
            and that you keep it alive with the connection.ping(reconnect=True)
            coroutine, as connections that are unused for over three minutes will be
            freed up for re-allocation.
        3) Allow your connection to be freed in the case of it not being needed, to
            reduce resource usage and requirements."""
    def __init__(self, bot):
        self.bot = bot
        self.event_loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("SVGEBot.DBConn")
        self.cog_config = None
        self.__open_connection_list = {}
        self.__get_config()
        self.__local_conn_pool = None
        self.logger.info("Loaded DBConnPool")

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

    @commands.Cog.listener()
    async def on_ready(self):
        await self.__acquire_db_pool()

    @tasks.loop(minutes=3)
    async def __ping_all_connections(self):
        """Ongoing task that slims down the number of connection objects within
        this pool, all free connections will be removed, every three minutes,
        all inactive connections that are not free (unused for over 2 minutes)
        will be released and made "free" connections, all connections that have
        been used minimally in the last three minutes will be pinged and have a
        reconnect attempt made."""
        # Close all "free" connections
        await self.__local_conn_pool.clear()
        # Iterate and release, or ping all "in-use" connections
        for connection, old_last_used in self.__open_connection_list.items():
            if connection.last_used - old_last_used > 120.0:
                # We're going to release connections that have been unused for
                # over 120 seconds.
                self.__local_conn_pool.release(connection)
                del self.__open_connection_list[connection]
                self.logger.info("Released connection due to inactivity")
            else:
                await connection.ping(reconnect=True)
                self.__open_connection_list[connection] = deepcopy(connection.last_usage)

    async def __acquire_db_pool(self):
        if self.__local_conn_pool is None:
            try:
                self.__local_conn_pool = await aiomysql.create_pool(
                    **self.cog_config["_db"]
                )
            except BaseException as connection_exception:
                self.logger.exception(f"Exception when connecting to database:"
                                      f"\n{connection_exception}\n\n "
                                      f"check your configuration and try again.")
                input()
                exit(1)

    async def acquire_db_connection(self):
        """Returns a database connection object, which will be handled by this pool handler
        object. When using connection objects offered by this pool handler, ensure you plan for
        the contingency where your connection object has been freed and you need to re-request
        it from the handler.

        :rtype: aiomysql.Connection()"""
        connection_to_offer = await self.__local_conn_pool.acquire()
        self.__open_connection_list[connection_to_offer] = deepcopy(connection_to_offer.last_usage)
        self.logger.info("Established database connection object")
        return connection_to_offer


def setup(bot):
    bot.add_cog(DBConnPool(bot))
