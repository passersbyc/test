"""
src/cli/commands/greet.py
示例命令：GreetCommand，用于演示如何实现一个新的 CLI 命令。
"""

import argparse
from src.cli.core import BaseCommand
from pathlib import Path
from toolboxs import logger

class GreetCommand(BaseCommand):
    """
    打招呼命令实现类。
    """
    
    def __init__(self) -> None:
        super().__init__()
        
    @property
    def name(self) -> str:
        """
        命令名称：greet
        """
        return "greet"

    @property
    def description(self) -> str:
        """
        命令描述：向用户打招呼。
        """
        return "向用户打招呼。"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置 greet 命令的参数。
        
        参数：
        - file_path: 必需的位置参数，指定文件路径。
        - --author: 作者名称。
        - --series: 系列名称。
        - --tags: 标签列表，逗号分隔。
        - --source_url: 来源 URL。
        - -n, --name: 要打招呼的对象名称（默认为 World）。
        - --loud: 是否大声打招呼（转换为全大写）。
        """
        # 辅助函数：校验是否为存在的文件
        def is_file(path_str):
            path = Path(path_str)
            if not path.exists():
                 raise argparse.ArgumentTypeError(f"文件 '{path_str}' 不存在")
            if not path.is_file():
                raise argparse.ArgumentTypeError(f"'{path_str}' 不是一个有效的文件路径")
            return path

        # 辅助函数：解析逗号分隔的标签
        def parse_tags(tags_str):
            if not tags_str:
                return []
            return [tag.strip() for tag in tags_str.split(',') if tag.strip()]

        # 位置参数：文件路径
        parser.add_argument(
            "file_path",
            type=is_file,
            help="目标文件的路径"
        )

        # 可选参数：元数据
        parser.add_argument(
            "--author",
            type=str,
            help="作者名称"
        )
        parser.add_argument(
            "--series",
            type=str,
            help="系列名称"
        )
        parser.add_argument(
            "--tags",
            type=parse_tags,
            help="标签列表，使用逗号分隔 (例如: 标签1,标签2)"
        )
        parser.add_argument(
            "--source_url",
            type=str,
            help="来源 URL 地址"
        )

        # 保留原有的参数
        parser.add_argument(
            "-n", "--name", 
            type=str, 
            default="World",
            help="用来打招呼的对象名称（默认为 World）"
        )
        parser.add_argument(
            "-l", "--loud", 
            action="store_true", 
            help="是否大声打招呼（转换为全大写）"
        )

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行打招呼逻辑。
        """
        # 打印元数据信息
        logger.info(f"📄 正在查阅文件: {args.file_path.absolute().name}")
        
        if args.author:
            logger.info(f"👤 作者大大: {args.author}")
        if args.series:
            logger.info(f"📚 所属系列: {args.series}")
        if args.tags:
            logger.info(f"🏷️ 贴有标签: {', '.join(args.tags)}")
        if args.source_url:
            logger.info(f"🔗 来源传送门: {args.source_url}")

        # 原有的打招呼逻辑
        message = f"👋 Hello, {args.name}!"
        if args.loud:
            message = f"📢 {message.upper()}!!!"
        logger.info(message)
            
        return 0
