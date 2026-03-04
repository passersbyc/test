import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Set
from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import KemonoDownloader
from toolboxs import get_project_root, logger

class FollowCommand(BaseCommand):
    """
    Follow 命令类。
    
    用于管理关注的作者列表，并自动获取他们的新作品链接添加到下载队列中。
    支持 Pixiv 和 Kemono 两个平台。
    """
    
    def __init__(self) -> None:
        """
        初始化 FollowCommand。
        
        设置下载器实例为 None (懒加载) 并加载黑名单。
        """
        super().__init__()
        self.args = None
        self._pixiv: Optional[PixivDownloader] = None
        self._kemono: Optional[KemonoDownloader] = None
        self._blacklist: Set[str] = set()
        self._load_blacklist()

    @property
    def pixiv(self) -> PixivDownloader:
        """
        获取 Pixiv 下载器实例 (单例/懒加载)。
        
        :return: PixivDownloader 实例
        """
        if self._pixiv is None:
            self._pixiv = PixivDownloader()
        return self._pixiv

    @property
    def kemono(self) -> KemonoDownloader:
        """
        获取 Kemono 下载器实例 (单例/懒加载)。
        
        :return: KemonoDownloader 实例
        """
        if self._kemono is None:
            self._kemono = KemonoDownloader()
        return self._kemono

    @property
    def name(self) -> str:
        """
        获取命令名称。
        
        :return: 命令名称字符串 "follow"
        """
        return "follow"

    @property
    def description(self) -> str:
        """
        获取命令描述。
        
        :return: 命令功能描述字符串
        """
        return "关注用户。"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置命令行参数解析器。
        
        :param parser: argparse.ArgumentParser 实例
        """
        parser.add_argument(
            "url",
            nargs="?",
            type=str,
            help="要关注作者/系列的网址。如果不提供此参数，则更新所有已关注作者的信息。"
        )

    def _load_blacklist(self) -> None:
        """
        从 blacklist.csv 加载黑名单链接。
        
        读取项目根目录下的 blacklist.csv 文件，将 URL 列的内容加载到 self._blacklist 集合中。
        """
        self._blacklist.clear()
        blacklist_path = get_project_root() / "blacklist.csv"
        if not blacklist_path.exists():
            return
        try:
            with open(blacklist_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                url_col = None
                if reader.fieldnames:
                    # 尝试匹配不同的列名
                    for col in ["URL", "url", "链接", "Link"]:
                        if col in reader.fieldnames:
                            url_col = col
                            break
                if url_col:
                    for row in reader:
                        u = row.get(url_col)
                        if u:
                            self._blacklist.add(u.strip())
        except Exception as e:
            logger.error(f"读取黑名单失败: {e}")

    def get_author_info(self, url: str) -> Optional[Tuple[str, int]]:
        """
        根据 URL 获取作者信息。
        
        :param url: 作者的主页链接
        :return: 包含 (作者名, 作品数量) 的元组，如果无法获取则返回 None
        """
        if not url:
            return None
        url = url.strip()
        if "pixiv.net" in url:
            return self.pixiv.get_author_info(url)
        elif "kemono" in url:
            return self.kemono.get_author_info(url)
        return None

    def get_author_works(self, url: str) -> List[str]:
        """
        获取作者的所有作品链接列表。
        
        :param url: 作者的主页链接
        :return: 作品链接列表
        """
        if not url:
            return []
        url = url.strip()
        if "pixiv.net" in url:
            return self.pixiv.get_user_works(url)
        elif "kemono" in url:
            return self.kemono.get_user_works(url)
        return []

    def _get_csv_path(self) -> Path:
        """
        获取关注列表 CSV 文件的路径。
        
        优先检查 follows.csv，如果不存在则检查 follow.csv (兼容旧版)。
        默认返回 follows.csv 的路径。
        
        :return: CSV 文件路径对象
        """
        root = get_project_root()
        csv_path = root / "follows.csv"
        if not csv_path.exists():
            alt_path = root / "follow.csv"
            if alt_path.exists():
                return alt_path
        return csv_path

    def update_follow_list(self, name: str, url: str, count: int) -> None:
        """
        更新关注列表 CSV 文件 (follows.csv)。
        
        如果 URL 已存在，则更新作者名、作品数和最后更新时间；
        如果 URL 不存在，则追加新记录。
        
        :param name: 作者名称
        :param url: 作者主页链接
        :param count: 作品总数
        """
        if not url:
            logger.warning("❓ 链接为空，跳过更新关注列表。")
            return
        url = url.strip()
        csv_path = self._get_csv_path()
        
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
                logger.error(f"读取关注列表失败: {e}")
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
            logger.info(f"📝 关注小本本已更新: {csv_path.name}")
        except Exception as e:
            logger.error(f"😫 无法写入关注列表: {e}")

    def update_downloads_list(self, works: List[str]) -> None:
        """
        将新发现的作品链接添加到下载队列 (downloadslist.csv)。
        
        不再直接过滤，而是根据状态进行标记：
        1. 在黑名单中的链接 -> 标记为 blacklisted
        2. 已在 library_manifest.csv (已下载库) 中的链接 -> 标记为 already_downloaded
        3. 新链接 -> 标记为 pending
        
        已在 downloadslist.csv 中的链接会保持原有状态，不会重复添加。
        
        :param works: 作品链接列表
        """
        if not works:
            return
            
        root = get_project_root()
        csv_path = root / "downloadslist.csv"
        
        # 1. 加载现有下载记录 (library_manifest.csv)
        downloaded_in_manifest = set()
        manifest_path = root / "library_manifest.csv"
        if manifest_path.exists():
             try:
                with open(manifest_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        src = row.get("来源") or row.get("source")
                        if src:
                            downloaded_in_manifest.add(src.strip())
             except Exception:
                 pass
                 
        # 2. 加载当前下载列表中的所有 URL (防止重复添加)
        current_list_urls = set()
        if csv_path.exists():
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames and "URL" in reader.fieldnames:
                        for row in reader:
                            u = row.get("URL")
                            if u:
                                current_list_urls.add(u.strip())
            except Exception:
                pass

        #logger.info(f"🔍 调试信息: 待处理 {len(works)} 个作品 | 黑名单 {len(self._blacklist)} 个 | 库中已存 {len(downloaded_in_manifest)} 个 | 列表已存 {len(current_list_urls)} 个")
        
        new_rows = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        for work_url in works:
            work_url = work_url.strip()
            
            # 如果已经在列表中，则跳过（保持原有状态）
            if work_url in current_list_urls:
                continue
                
            # 判定状态
            status = "pending"
            if work_url in self._blacklist:
                status = "blacklisted"
                logger.info(f"标记(黑名单): {work_url}")
            elif work_url in downloaded_in_manifest:
                status = "already_downloaded"
                logger.info(f"标记(已下载): {work_url}")
            else:
                logger.info(f"添加(待下载): {work_url}")
                
            new_rows.append([work_url, now, status])
            current_list_urls.add(work_url) # 防止本次批量处理中有重复

        if not new_rows:
            logger.info("✨ 所有作品都已在清单中记录，无需更新。")
            return

        file_exists = csv_path.exists()
        try:
            with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                # 如果文件不存在，写入表头
                if not file_exists:
                    writer.writerow(["URL", "Added Date", "Status"])
                writer.writerows(new_rows)
            logger.info(f"📝 已更新下载队列: 新增 {len(new_rows)} 条记录 (含标记)")
        except Exception as e:
            logger.error(f"写入下载队列失败: {e}")

    def process_author(self, url: str) -> None:
        """
        处理单个作者的完整流程。
        
        1. 获取作者信息 (API)
        2. 更新关注列表 (follows.csv)
        3. 获取作者所有作品链接 (API)
        4. 将新作品添加到下载队列 (downloadslist.csv)
        
        :param url: 作者主页链接
        """
        # 移除重复日志，因为 process_url 内部会打印
        # logger.info(f"🔍 正在解析神秘链接: {url}")
        
        try:
            # 1. 获取作者信息
            info = self.get_author_info(url)
            if not info:
                logger.warning("❓ 好像没找到这位作者呢，请确认链接是否正确哦。")
                return
            
            name, count = info
            logger.info(f"💖 成功捕获一只作者: {name} (作品数: {count})")
            
            # 2. 更新关注列表
            self.update_follow_list(name, url, count)
            
            # 3. 获取所有作品链接
            # logger.info("📚 正在获取作品列表...") # 移除重复日志
            
            works = []
            if "pixiv.net" in url:
                # 使用 process_url 的 dry_run 模式或者仅利用其解析逻辑
                # 但 PixivDownloader 的 process_url 混合了下载逻辑
                # 为了保持 follow 命令 "只爬取链接不下载" 的原则
                # 我们依然调用 get_user_works
                works = self.pixiv.get_user_works(url)
            elif "kemono" in url:
                works = self.kemono.get_user_works(url)
                
            logger.info(f"📄 找到 {len(works)} 个作品链接")
            
            # 4. 更新下载队列 (自动过滤黑名单和已下载)
            self.update_downloads_list(works)
            
        except Exception as e:
            logger.error(f"💥 处理出错: {e}")

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行 follow 命令。
        
        如果命令行参数提供了 URL，则只处理该 URL。
        如果未提供 URL，则读取 follows.csv 并更新所有已关注作者的信息。
        
        :param args: 命令行参数命名空间
        :return: 退出代码 (0 表示成功，1 表示失败)
        """
        # 如果未提供 URL，则更新所有已关注作者
        if not args.url:
            logger.info("🔄 正在拜访所有已关注的作者...")
            csv_path = self._get_csv_path()
            
            if not csv_path.exists():
                logger.info("📭 关注列表是空的呢，快去关注喜欢的作者吧！")
                return 0
                
            urls = []
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        u = row.get("URL")
                        if u:
                            urls.append(u.strip())
            except Exception as e:
                logger.error(f"😵 读取关注列表失败: {e}")
                return 1
            
            urls = [u for u in dict.fromkeys(urls) if u]
            if not urls:
                logger.info("📭 关注列表是空的呢。")
                return 0
                
            logger.info(f"📋 发现 {len(urls)} 位特别关注，开始逐一确认...")
            for idx, url in enumerate(urls, 1):
                logger.info(f"[{idx}/{len(urls)}] 正在连线: {url}")
                self.process_author(url)
            
            logger.info("🎉 所有关注信息都更新好啦！")
            return 0

        # 如果提供了 URL，则处理单个
        url = args.url.strip() if args.url else ""
        if not url:
            logger.warning("❓ 链接不能为空哦。")
            return 1
        
        self.process_author(url)
        return 0
