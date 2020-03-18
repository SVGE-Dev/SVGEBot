import asyncio
import logging
import os
import pytz
import json
from datetime import datetime
from shutil import copyfile

import discord
from discord.ext import commands

svgebot = commands.Bot(command_prefix="placeholder")


@svgebot.event
async def on_ready():
    logger.info("Bot ready")


@svgebot.event
async def on_message(message):
    if message.author.bot:
        return
    await svgebot.process_commands(message)


if __name__ == "__main__":
    # Logging configuration

    dt_now_formatted = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if not os.path.exists("./logs/"):
        os.mkdir("./logs/")
        print("Created ./logs/ folder for persistent logging")

    logger = logging.getLogger("SVGEBot")
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('[{asctime}] [{levelname:}] {name}: {message}',
                                  '%Y-%m-%d %H:%M:%S', style='{')

    file_log = logging.FileHandler(f"./logs/svgebot_log_{dt_now_formatted}.log",
                                   encoding="utf-8", mode="w")
    console_log = logging.StreamHandler()

    file_log.setFormatter(formatter)
    console_log.setFormatter(formatter)

    logger.addHandler(file_log)
    logger.addHandler(console_log)

    logger.info("Logging ready")

    if not os.path.exists("./config/temp_config.json"):
        copyfile("./config/temp_config_default.json", "./config/temp_config.json")
        logger.warning("Config was missing, copied config template")

    with open("./config/temp_config.json", "r") as config_file:
        temp_config_json = json.load(config_file)
    if temp_config_json["bot"]["delete_msg_after"] == -1:
        temp_config_json["bot"]["delete_msg_after"] = None
    logger.info("Loaded config variables")

    svgebot.command_prefix = temp_config_json["bot"]["cmd_prefix"]
    logger.info(f"Set command prefix to: {temp_config_json['bot']['cmd_prefix']}")

    cogs_loaded_counter = 0
    for cog_to_load in os.listdir("./extensions/"):
        if cog_to_load.endswith(".py"):
            logger.info(f"Found {cog_to_load[:-3]}")
            if f"extensions.{cog_to_load[:-3]}" in temp_config_json["autoload extensions"]:
                try:
                    svgebot.load_extension(f"extensions.{cog_to_load[:-3]}")
                    cogs_loaded_counter += 1
                except Exception as e:
                    logger.warning(f"Failed to load extension: {cog_to_load[:-3]}\n\n"
                                   f"{e}")
    if cogs_loaded_counter != 0:
        logger.info(f"Found and autoloaded {cogs_loaded_counter} extension(s)")
    else:
        logger.warning("Autoloaded no extensions in ./extensions/, this will cause "
                       "major losses in bot functionality")

    logger.info("Bot process starting")

    try:
        svgebot.run(temp_config_json["bot"]["token"], reconnect=True)
    except discord.LoginFailure as login_failure_e:
        logger.exception(f"Bot login was not completed:\n{login_failure_e}")
        input()
