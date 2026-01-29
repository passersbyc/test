"""
src/cli/main.py
CLI 主入口点和命令自动加载模块。
"""

import sys
import pkgutil
import importlib
import inspect
import json
from types import ModuleType
from typing import Iterator
from pathlib import Path
from src.cli.core import CLIApp, BaseCommand
import src.cli.commands
from toolboxs import get_library_path

def load_commands(app: CLIApp) -> None:
    """
    从 commands 包中动态加载所有命令。
    此函数遍历 src.cli.commands 包中的所有模块，
    并尝试将其中继承自 BaseCommand 的类注册到 CLI 应用中。
    
    Args:
        app: CLIApp 实例，用于注册发现的命令。
    """
    
    # 遍历 src.cli.commands 包中的所有模块
    package = src.cli.commands
    prefix = package.__name__ + "."
    
    for _, name, _ in pkgutil.iter_modules(package.__path__, prefix):
        try:
            # 动态导入模块
            module = importlib.import_module(name)
            # 在模块中查找并注册命令类
            _register_commands_from_module(app, module)
        except ImportError as e:
            # 如果模块导入失败，打印警告但不中断程序
            print(f"Warning: Failed to load command module '{name}': {e}", file=sys.stderr)

def _register_commands_from_module(app: CLIApp, module: ModuleType) -> None:
    """
    在指定模块中查找并注册 BaseCommand 的子类。
    此函数使用反射来检查模块中的所有成员，
    找到继承自 BaseCommand 的类并将其注册到应用中。
    
    Args:
        app: CLIApp 实例。
        module: 要扫描的 Python 模块对象。
    """
    for name, obj in inspect.getmembers(module):
        # 检查对象是否为 BaseCommand 的子类（排除 BaseCommand 自身）
        if (inspect.isclass(obj) and 
            issubclass(obj, BaseCommand) and 
            obj is not BaseCommand):
            try:
                # 注册命令类
                app.register_command(obj)
            except ValueError as e:
                # 处理重复注册等错误，打印警告但不中断程序
                print(f"Warning: {e}", file=sys.stderr)

def main() -> int:
    """
    CLI 应用的主函数。
    创建 CLIApp 实例，加载所有命令，然后运行应用。
    
    Returns:
        应用执行的退出码。
    """
    # 创建 CLI 应用实例，设置程序名为 'passersbyc'
    app = CLIApp(prog_name="passersbyc")
    # 动态加载所有命令
    load_commands(app)
    # 启动应用
    library_path = get_library_path()
    # 确保库目录存在
    library_path.mkdir(parents=True, exist_ok=True)

    return app.run()

if __name__ == "__main__":
    # 当作为脚本直接运行时，调用主函数并传递系统参数
    sys.exit(main())
