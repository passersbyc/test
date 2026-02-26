import argparse
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import KemonoDownloader
from typing import List

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
            print("❌ 未提供有效网址")
            return 1
        
        for u in urls:
            print(f"--------------------------------------------------")
            print(f"🔗 开始处理: {u}")
            
            if "pixiv.net" in u:
                # 统一使用 process_url 处理所有类型的 Pixiv URL
                stats = self._pixiv.process_url(u)
                if stats["success"] == 0 and stats["skipped"] == 0 and stats["failed"] == 0:
                     print("❌ 处理似乎未执行任何操作，请检查 URL 是否有效。")
            elif "kemono.cr" in u:
                stats = self._kemono.process_url(u)
                if stats["success"] == 0 and stats["skipped"] == 0 and stats["failed"] == 0:
                    print("❌ 处理似乎未执行任何操作，请检查 URL 是否有效。")
            else:
                print("⚠️ 暂不支持该网址，目前仅支持 pixiv.net 与 kemono.cr")
                
        return 0
