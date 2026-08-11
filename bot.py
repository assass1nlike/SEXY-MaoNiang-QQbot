"""NoneBot application entry point."""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter


nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
nonebot.load_plugin("plugins.auto_reply")
nonebot.load_plugin("plugins.status_notify")


if __name__ == "__main__":
    nonebot.run()
