import asyncio
import logging
import os
import pytz
import json
from copy import deepcopy
from datetime import datetime
from shutil import copyfile

import discord
from discord.ext import commands

svgebot = commands.Bot(command_prefix="placeholder")


@svgebot.event
async def on_ready():
    logger.info("SVGEBot ready")


@svgebot.event
async def on_message(message):
    if message.author.bot:
        return
    try:
        if message.content.startswith(svgebot.bot_config["cmd_prefix"]):
            async with message.channel.typing():
                await message.delete()
                logger.debug(f"Command: '{message.content}' from: '{message.author}'")
                await svgebot.process_commands(message)
    except commands.errors.CheckFailure as check_fail:
        logger.debug("User {0} sent the command {1}, which failed "
                     "command checks with: \n{2}".format(message.author,
                                                         message.content,
                                                         check_fail))
        await message.channel.send("You do not have the permissions "
                                   "required for this command",
                                   delete_after=svgebot.delete_msg_after)
    except commands.errors.CommandNotFound as cmd_not_found:
        logger.debug(f"Command Not Found Error: \n{cmd_not_found}.")
        await message.channel.send(f'Command "{message.content}" does not exist.',
                                   delete_after=svgebot.delete_msg_after)


if __name__ == "__main__":
    # Logging configuration

    dt_now_formatted = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if not os.path.exists("./logs/"):
        os.mkdir("./logs/")
        print("Created ./logs/ folder for persistent logging")

    logger = logging.getLogger("SVGEBot")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('[{asctime}] [{levelname:}] {name}: {message}',
                                  '%Y-%m-%d %H:%M:%S', style='{')

    file_log = logging.FileHandler(f"./logs/svgebot_log_{dt_now_formatted}.log",
                                   encoding="utf-8", mode="w")
    console_log = logging.StreamHandler()

    file_log.setFormatter(formatter)
    console_log.setFormatter(formatter)

    logger.addHandler(file_log)
    logger.addHandler(console_log)

    logger.debug("Logging ready")

    if not os.path.exists("./config/temp_config.json"):
        copyfile("./config/temp_config_default.json", "./config/temp_config.json")
        logger.warning("Config was missing, copied config template")

    with open("./config/temp_config.json", "r") as config_file:
        temp_config_json = json.load(config_file)
    if temp_config_json["bot"]["delete_msg_after"] == -1:
        temp_config_json["bot"]["delete_msg_after"] = None
    logger.debug("Loaded config variables")

    svgebot.command_prefix = temp_config_json["bot"]["cmd_prefix"]
    logger.info(f"Set command prefix to: {temp_config_json['bot']['cmd_prefix']}")

    config_for_bot_dist = deepcopy(temp_config_json)
    # Remove the token from the data that's distributed to cogs
    del config_for_bot_dist["bot"]["token"]

    svgebot.bot_config = config_for_bot_dist["bot"]
    svgebot.delete_msg_after = config_for_bot_dist["bot"]["delete_msg_after"]

    cogs_loaded_counter = 0
    for cog_to_load in os.listdir("./extensions/"):
        if cog_to_load.endswith(".py"):
            logger.debug(f"Found {cog_to_load[:-3]}")
            if f"extensions.{cog_to_load[:-3]}" in temp_config_json["autoload extensions"]:
                try:
                    svgebot.load_extension(f"extensions.{cog_to_load[:-3]}")
                    cogs_loaded_counter += 1
                except Exception as e:
                    logger.warning(f"Failed to load extension: {cog_to_load[:-3]}\n\n"
                                   f"{e}")
    if cogs_loaded_counter != 0:
        logger.debug(f"Auto-loaded {cogs_loaded_counter} extension(s)")
    else:
        logger.warning("Autoloaded no extensions in ./extensions/, this will cause "
                       "major losses in bot functionality")

    logger.debug("Bot process starting")

    try:
        svgebot.run(temp_config_json["bot"]["token"], reconnect=True)
    except discord.LoginFailure as login_failure_e:
        logger.exception(f"Bot login was not completed:\n{login_failure_e}")
        input()
