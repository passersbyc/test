import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import Kemono
from toolboxs import get_project_root


class FollowCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()
        self.args = None

    @property
    def name(self) -> str:
        """
        命令名称：follow
        """
        return "follow"

    @property
    def description(self) -> str:
        """
        命令描述：关注用户。
        """
        return "关注用户。"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置 follow 命令的参数。
        """
        parser.add_argument(
            "url",
            nargs="?",
            type=str,
            help="要关注作者/系列的网址。如果不提供此参数，则更新所有已关注作者的信息。"
        )

    def get_author_info(self, url: str) -> Optional[Tuple[str, int]]:
        """
        根据 URL 获取作者信息 (作者名, 作品数量)。
        """
        if "pixiv.net" in url:
            downloader = PixivDownloader()
            return downloader.get_author_info(url)
        elif "kemono" in url:
            downloader = Kemono()
            return downloader.get_author_info(url)
        return None

    def update_follow_list(self, name: str, url: str, count: int) -> None:
        """
        更新关注列表 CSV 文件。
        如果 URL 已存在，则更新信息；否则追加新记录。
        """
        root = get_project_root()
        csv_path = root / "follows.csv"
        
        headers = ["Author", "URL", "Works Count", "Last Updated"]
        rows = []
        updated = False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 读取现有数据
        if csv_path.exists():
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # 兼容旧表头或不同格式，确保 url 存在
                        if row.get("URL") == url:
                            row["Author"] = name
                            row["Works Count"] = str(count)
                            row["Last Updated"] = now
                            updated = True
                        rows.append(row)
            except Exception as e:
                print(f"读取关注列表失败: {e}")
                return

        # 如果未找到现有记录，则追加
        if not updated:
            rows.append({
                "Author": name,
                "URL": url,
                "Works Count": str(count),
                "Last Updated": now
            })

        # 写入文件
        try:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            print(f"已更新关注列表: {csv_path}")
        except Exception as e:
            print(f"写入关注列表失败: {e}")

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行关注用户逻辑。
        
        Args:
            args: 包含解析后的参数。
            
        Returns:
            0 表示执行成功。
        """
        # 如果未提供 URL，则更新所有已关注作者
        if not args.url:
            print("正在更新所有已关注作者的信息...")
            root = get_project_root()
            csv_path = root / "follows.csv"
            
            if not csv_path.exists():
                print("未找到关注列表，请先关注作者。")
                return 0
                
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
                return 1
            
            if not urls:
                print("关注列表为空。")
                return 0
                
            print(f"找到 {len(urls)} 位关注作者，开始更新...")
            for idx, url in enumerate(urls, 1):
                print(f"[{idx}/{len(urls)}] 正在更新: {url}")
                info = self.get_author_info(url)
                if info:
                    name, count = info
                    print(f"  -> {name} (作品数量: {count})")
                    self.update_follow_list(name, url, count)
                else:
                    print(f"  -> 无法获取信息: {url}")
            
            print("所有作者更新完毕！")
            return 0

        # 如果提供了 URL，则处理单个
        url = args.url
        print(f"正在分析链接: {url}")
        
        info = self.get_author_info(url)
        if info:
            name, count = info
            print(f"成功获取作者信息: {name} (作品数量: {count})")
            self.update_follow_list(name, url, count)
        else:
            print("无法获取作者信息，请确认链接是否正确且为作者主页。")
        
        return 0
