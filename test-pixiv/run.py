"""
run.py
项目的启动脚本。
负责配置 Python 路径并启动 CLI 应用。
"""

import sys
import os

# 将 src 目录添加到 Python 的搜索路径中，
# 这样可以直接使用 'from src.cli...' 进行导入。
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

from src.cli.main import main

if __name__ == "__main__":
    # 执行主函数，并根据返回的退出码退出程序
    sys.exit(main())
