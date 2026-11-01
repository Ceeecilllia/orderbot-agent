"""启动入口：panel serve app.py --show。"""
from pathlib import Path
from dotenv import load_dotenv
from orderbot.ui import create_app

load_dotenv(Path(__file__).with_name('.env'), override=False)  # 仅读取标准 .env；不会导入旧 mykeys.py。
create_app().servable()
