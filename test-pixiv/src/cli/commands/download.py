import argparse
from typing import List, Optional

from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import KemonoDownloader
from toolboxs import logger, delete_downloads_file

class DownloadCommand(BaseCommand):
    """
    下载命令实现类。
    """
    def __init__(self) -> None:
        super().__init__()
        self.args: Optional[argparse.Namespace] = None
        # 懒加载下载器实例，避免在不需要下载时初始化资源
        self._pixiv: Optional[PixivDownloader] = None
        self._kemono: Optional[KemonoDownloader] = None

    @property
    def pixiv(self) -> PixivDownloader:
        if self._pixiv is None:
            self._pixiv = PixivDownloader()
        return self._pixiv

    @property
    def kemono(self) -> KemonoDownloader:
        if self._kemono is None:
            self._kemono = KemonoDownloader()
        return self._kemono

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
        return "通过网址下载资源 (支持 Pixiv 和 Kemono)。"
        
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
            logger.warning("📭 哎呀？没有收到任何有效的链接呢。")
            return 1
        
        logger.info(f"📦 收到 {len(urls)} 个传送请求，准备出发！")
        
        success_count = 0
        failed_count = 0
        
        for u in urls:
            logger.info("-" * 50)
            logger.info(f"🔗 正在解析传送门: {u}")  
            
            stats = {"success": 0, "failed": 0, "skipped": 0}
            try:
                if "pixiv.net" in u:
                    stats = self.pixiv.process_url(u)
                elif "kemono.cr" in u:
                    stats = self.kemono.process_url(u)
                else:
                    logger.warning(f"🚫 抱歉呀，目前的魔法只支持 pixiv.net 和 kemono.cr 哦: {u}")
                    failed_count += 1
                    continue
            except Exception as e:
                logger.error(f"💥 处理链接时发生了意外: {u} | 错误: {e}")
                stats["failed"] += 1

            if stats["success"] > 0:
                success_count += 1
            elif stats["failed"] > 0:
                failed_count += 1
            elif stats["skipped"] > 0:
                # 跳过不算失败也不算成功（通常是因为已存在）
                pass
            else:
                # 既没有成功也没有失败也没有跳过，可能是链接格式不支持
                logger.warning("❓ 这个链接好像有点奇怪，什么都没发生哦。")
                
        # 清理临时下载文件
        delete_downloads_file()
        
        logger.info("=" * 50)
        logger.info(f"✨ 所有任务都处理完啦！总计处理: {len(urls)} | 成功: {success_count} | 失败: {failed_count}")
        
        return 0 if failed_count == 0 else 1
