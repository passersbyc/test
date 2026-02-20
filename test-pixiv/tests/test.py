import json
import sys
from pathlib import Path

# Add project root to sys.path so we can import toolboxs
sys.path.append(str(Path(__file__).parent.parent))

from toolboxs import generate_file_md5

# 确保当前工作目录是项目根目录
root_dir = Path(__file__).parent.parent
config_path = root_dir / "config.json"

with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)
print(config["filetype"])

print(generate_file_md5(r"C:\Users\Administrator\Desktop\暂存\小说\萌冬酱\欲网性牢\test1.txt"))
print(generate_file_md5(r"C:\Users\Administrator\Desktop\暂存\小说\萌冬酱\欲网性牢\test2.txt"))
