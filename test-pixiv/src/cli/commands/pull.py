import argparse
import csv
from pathlib import Path
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import Kemono
from toolboxs import get_project_root

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
            print(f"错误：{csv_path} 不存在。请先关注用户。")
            return

        urls = []
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = row.get("URL")
                    if u:
                        urls.append(u)
        except Exception as e:
            print(f"读取关注列表失败: {e}")
            return

        if not urls:
            print("关注列表为空。")
            return

        print(f"找到 {len(urls)} 位关注作者，开始拉取最新作品...")
        
        for idx, url in enumerate(urls, 1):
            print(f"\n[{idx}/{len(urls)}] 正在处理: {url}")
            if "pixiv.net" in url:
                downloader = PixivDownloader()
                downloader.process_url(url)
            elif "kemono" in url:
                downloader = Kemono()
                downloader.process_url(url)
            else:
                print(f"未知链接类型: {url}")
                return
        
        print("\n所有关注作者处理完毕！")