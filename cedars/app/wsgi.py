"""Create flask application"""

import os
import sys
from dotenv import dotenv_values, load_dotenv
from loguru import logger
from app import create_app

load_dotenv()

environment = os.getenv('ENV', 'local')
config = dotenv_values(".env")

logger.remove()
logger.add("cedars_{time}.log",
           rotation="1 day", enqueue=True, level="INFO", format="{time} - {level} - {message}")
logger.add(sys.stderr, format="{time} {level} {message}", filter="cedars", level="INFO", colorize=True)

app = create_app(f"config.{environment.title()}")

if __name__ == '__main__':
    # host should be 0.0.0.0 for docker to work
    logger.info(f"Starting app in {environment} mode")
    app.run(host=config['HOST'], debug=True)
