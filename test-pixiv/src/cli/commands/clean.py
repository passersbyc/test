import argparse
from src.cli.core import BaseCommand
from pathlib import Path
import json
import shutil
from toolboxs import logger

class CleanCommand(BaseCommand):
    """
    清理命令实现类。
    """
    def __init__(self) -> None:
        super().__init__()
        self.args = None  # 用于存储解析后的参数
        self.data = self.config

    @property
    def name(self) -> str:
        """
        命令名称：clean
        """
        return "clean"

    @property
    def description(self) -> str:
        """
        命令描述：清理项目中的临时文件和缓存。
        """
        return "清理项目中的临时文件和缓存。"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置命令行参数解析器。
        """
        parser.add_argument(
            "-l","--library",
            action="store_true",
            help="清理库文件。"
        )
        parser.add_argument(
            "-c","--csv",
            action="store_true",
            help="清理 CSV 文件。"
        )
        parser.add_argument(
            "-a","--all",
            action="store_true",
            help="清除所有内容（元数据、库、CSV、缓存）。"
        )
        parser.add_argument(
            "-q","--query",
            type=Path,
            nargs='?',
            const=Path.cwd(),
            default=Path.cwd(),
            help="清除所有的文件和缓存。"
        )
        parser.add_argument(
            "-f","--force",
            action="store_true",
            help="强制清理，不提示确认。"
        )

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行清理操作。
        """
        self.args = args  # 保存 args 以便在子方法中使用 force 参数
        
        # 如果指定了 --all，则执行全量清理
        if args.all:
            self._clean_all(args.query)
            return 0
        
        # 标记是否执行了特定的清理操作
        specific_action = False
        
        if args.library:
            self._clean_library(args.query)
            specific_action = True
        if args.csv:
            self._clean_csv(args.query)
            specific_action = True
            
        # 无论是否执行了特定清理，最后都执行一次通用的缓存清理
        self._clean_query(args.query)

        return 0

    def _confirm(self, message: str) -> bool:
        """
        请求用户确认。
        """
        if self.args.force:
            return True
        response = input(f"{message} (y/n): ").lower()
        return response == 'y'

    def _clean_library(self, query: Path) -> None:
        """
        清理库目录。
        """
        library_path_str = self.data.get("project_settings", {}).get("library_path", "library")
        library_path = Path(library_path_str)
        if not library_path.is_absolute():
            from toolboxs import get_project_root
            library_path = get_project_root() / library_path

        if not library_path.exists():
            logger.warning(f"👻 咦？库目录好像不在这里: {library_path}")
            return

        if self._confirm(f"🔥 真的要清空整个大书库 {library_path} 吗？(里面的书都会消失哦!)"):
            logger.info("🧹 开始大扫除啦...")
            deleted_count = 0
            for item in library_path.iterdir():
                if item.is_dir() and item.name != ".meta":
                    shutil.rmtree(item, ignore_errors=True)
                    logger.info(f"🗑️  拜拜了文件夹: {item.name}")
                    deleted_count += 1
            if deleted_count > 0:
                logger.info(f"✨ 书库变得干干净净啦！共清理了 {deleted_count} 个文件夹。")
            else:
                logger.info("✨ 书库本来就很干净呢~")
        else:
            logger.info("🚫 清理操作已取消。")




    def _clean_csv(self, query: Path) -> None: 
        """
        清理生成的 CSV 清单文件。
        """
        csv_path_str = self.data.get("project_settings", {}).get("csv_path", "library_manifest.csv")
        csv_path = Path(csv_path_str)
        if not csv_path.is_absolute():
            from toolboxs import get_project_root
            csv_path = get_project_root() / csv_path

        if csv_path.exists():
            if self._confirm(f"📜 确定要撕毁这张清单 {csv_path} 吗?"):
                csv_path.unlink()
                logger.info(f"✅ 咔嚓！清单文件已销毁: {csv_path.name}")
        else:
            logger.warning(f"🍃 找不到清单文件呢: {csv_path.name}")

    def _clean_query(self, query: Path) -> None:
        """
        通用清理：清除缓存文件 (__pycache__, .pyc, .DS_Store 等)。
        """
        
        targets = ["__pycache__", "*.pyc", "*.pyo", ".DS_Store", ".pytest_cache"]
        logger.info(f"🔍 正在寻找角落里的灰尘 ({', '.join(targets)}) ...")
        
        count = 0
        for pattern in targets:
            for item in query.rglob(pattern):
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    count += 1
                except Exception as e:
                    logger.warning(f"💦 哎呀，擦不掉 {item.name}: {e}")
        
        if count > 0:
            logger.info(f"✨ 呼~ 打扫完毕！清理了 {count} 处灰尘。")
        else:
            logger.info("✨ 哇，这里一尘不染呢！")

    def _clean_all(self, query: Path) -> None:
        """
        清理所有内容（元数据、库、CSV、缓存）。
        """
        logger.warning("🚨 高能预警！正在启动【毁灭模式】全量清理...")
        self._clean_library(query)
        self._clean_csv(query)
        self._clean_query(query)
        logger.info("🌈 世界清静了，全量清理完成！")