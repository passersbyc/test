import argparse
import csv
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import Kemono
from toolboxs import get_project_root, logger, delete_downloads_file

class PullCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()
    
    @property
    def description(self) -> str:
        """
        命令描述：从follow.csv中下载关注的用户的最新作品。
        """
        return "从follow.csv中下载关注的用户的最新作品。"
    
    @property
    def name(self) -> str:
        """
        命令名称：pull
        """
        return "pull"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置 pull 命令的参数。
        """
        pass

    def execute(self, args: argparse.Namespace) -> None:
        """
        执行 pull 命令。
        从 follow.csv 读取关注列表，下载每个用户的最新作品。
        """
        root = get_project_root()
        csv_path = root / "follows.csv"
        if not csv_path.exists():
            logger.info(f"📭 哎呀，关注列表 {csv_path} 不见啦。请先关注一些作者吧！")       
            return

        urls = []
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = row.get("URL")
                    if u:
                        urls.append(u.strip())
        except Exception as e:
            logger.error(f"😵 读取关注列表时出错了: {e}")
            return

        unique_urls = [u for u in dict.fromkeys(urls) if u]
        if not unique_urls:
            logger.info("📭 关注列表是空的呢。")
            return

        pixiv_downloader = PixivDownloader()
        kemono_downloader = Kemono()
        logger.info(f"📋 发现 {len(unique_urls)} 位特别关注，正在查看他们有没有新作品...")
        
        processed = 0
        failed = 0
        unknown = 0
        
        # 预先初始化下载器实例
        pixiv_downloader = PixivDownloader()
        kemono_downloader = Kemono()
        
        try:
            for idx, url in enumerate(unique_urls, 1):
                logger.info(f"[{idx}/{len(unique_urls)}] 正在拜访: {url}")
                try:
                    lower_url = url.lower()
                    if "pixiv.net" in lower_url:
                        pixiv_downloader.process_url(url)
                        processed += 1
                    elif "kemono" in lower_url:
                        kemono_downloader.process_url(url)
                        processed += 1
                    else:
                        logger.warning(f"❓ 这个链接好像不认识呢: {url}")
                        unknown += 1
                except Exception as e:
                    logger.error(f"💥 处理失败: {url} - {e}")
                    failed += 1
                        
        finally:
            delete_downloads_file()
            logger.info(f"✨ 所有关注作者都拜访完啦！成功 {processed} | 失败 {failed} | 未知 {unknown}")
