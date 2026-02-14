import argparse
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from typing import List

class DownloadCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()
        self.args = None
        self._pixiv = PixivDownloader()

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
            help="要下载的资源网址（支持 pixiv 插画/漫画/小说）"
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
            print(f"正在处理: {u}")
            if "pixiv.net" in u:
                res = self._pixiv.download_and_import(u)
                if res:
                    print(f"✅ 下载并导入完成: {res}")
                else:
                    print("⏭️ 已存在相同来源或下载失败，跳过")
            else:
                print("⚠️ 暂不支持该网址")
        return 0
