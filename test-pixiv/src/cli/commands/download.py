import argparse
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import KemonoDownloader
from typing import List
from toolboxs import logger, delete_downloads_file

class DownloadCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()
        self.args = None
        self._pixiv = PixivDownloader()
        self._kemono = KemonoDownloader()

    @property
    def name(self) -> str:
        """
        命令名称：download
        """
        return "download"

    @property
    def description(self) -> str:
        """
        命令描述：通过网址下载资源。
        """
        return "通过网址下载资源。"
        
    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置命令行参数解析器。
        """
        parser.add_argument(
            "urls",
            type=str,
            nargs='+',
            help="要下载的资源网址（支持 pixiv 与 kemono）"
        )

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行下载命令。
        """
        self.args = args
        urls: List[str] = [u.strip() for u in args.urls if u.strip()]
        if not urls:
            logger.info("📭 哎呀？没有收到任何链接呢。")
            return 1
        
        logger.info(f"📦 收到 {len(urls)} 个传送请求，准备出发！")
        
        for u in urls:
            logger.info(f"--------------------------------------------------")
            logger.info(f"🔗 正在解析传送门: {u}")  
            
            if "pixiv.net" in u:
                # 统一使用 process_url 处理所有类型的 Pixiv URL
                stats = self._pixiv.process_url(u)
                if stats["success"] == 0 and stats["skipped"] == 0 and stats["failed"] == 0:
                    logger.info("❓ 这个链接好像有点奇怪，什么都没发生哦。")
            elif "kemono.cr" in u:
                stats = self._kemono.process_url(u)
                if stats["success"] == 0 and stats["skipped"] == 0 and stats["failed"] == 0:
                    logger.info("❓ 这个链接好像有点奇怪，什么都没发生哦。")
            else:
                logger.info("🚧 抱歉呀，目前的魔法只支持 pixiv.net 和 kemono.cr 哦。")
        delete_downloads_file()
        logger.info("✨ 所有任务都处理完啦！")
        return 0
