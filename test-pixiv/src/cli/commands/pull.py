import argparse
import csv
from typing import Optional, List
from pathlib import Path

from src.cli.core import BaseCommand
from src.cli.downplugin.pixiv import PixivDownloader
from src.cli.downplugin.kemono import KemonoDownloader
from toolboxs import get_project_root, logger

class PullCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()
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
    def description(self) -> str:
        """
        命令描述：从 downloadslist.csv 中下载待处理的作品。
        """
        return "从 downloadslist.csv 中下载待处理的作品。"
    
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

    def _save_list(self, path: Path, fieldnames: List[str], rows: List[dict]) -> None:
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        except Exception as e:
            logger.error(f"保存进度失败: {e}")

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行 pull 命令。
        从 downloadslist.csv 读取待下载列表，下载并更新状态。
        """
        root = get_project_root()
        csv_path = root / "downloadslist.csv"
        
        if not csv_path.exists():
            logger.info("📭 下载列表是空的呢 (downloadslist.csv 不存在)。")
            return 0

        rows = []
        fieldnames = []
        
        # 1. 读取列表
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames if reader.fieldnames else []
                # 确保有 Status 列
                if "Status" not in fieldnames:
                    fieldnames.append("Status")
                
                for row in reader:
                    rows.append(row)
        except Exception as e:
            logger.error(f"😵 读取下载列表时出错了: {e}")
            return 1

        if not rows:
            logger.info("📭 下载列表是空的呢。")
            return 0

        # 统计各状态数量
        stats_counter = {
            "pending": 0,
            "done": 0,
            "failed": 0,
            "blacklisted": 0,
            "already_downloaded": 0,
            "other": 0
        }
        
        pending_indices = []
        
        for idx, row in enumerate(rows):
            status = row.get("Status", "pending")
            # 处理空状态默认为 pending
            if not status:
                status = "pending"
            
            if status == "pending":
                stats_counter["pending"] += 1
                pending_indices.append(idx)
            elif status == "done":
                stats_counter["done"] += 1
            elif status == "failed":
                stats_counter["failed"] += 1
            elif status == "blacklisted":
                stats_counter["blacklisted"] += 1
            elif status == "already_downloaded":
                stats_counter["already_downloaded"] += 1
            else:
                stats_counter["other"] += 1

        logger.info(f"📊 任务队列概览: 待下载 {stats_counter['pending']} | 已完成 {stats_counter['done']} | 失败 {stats_counter['failed']} | 黑名单 {stats_counter['blacklisted']} | 库中已存 {stats_counter['already_downloaded']}")

        if not pending_indices:
            logger.info("✨ 所有任务都已完成啦！无需执行下载。")
            return 0

        logger.info(f"🚀 准备处理 {len(pending_indices)} 个待下载任务...")
        
        # 收集 URL
        pixiv_urls = []
        kemono_urls = []
        
        # 记录 URL 对应的行索引，方便后续更新状态
        # key: url, value: list of indices (以防重复)
        url_to_indices = {}

        for idx in pending_indices:
            row = rows[idx]
            url = row.get("URL")
            if not url:
                continue
            
            u_clean = url.strip()
            if not u_clean:
                continue
                
            if u_clean not in url_to_indices:
                url_to_indices[u_clean] = []
            url_to_indices[u_clean].append(idx)
            
            lower_url = u_clean.lower()
            if "pixiv.net" in lower_url:
                pixiv_urls.append(u_clean)
            elif "kemono" in lower_url:
                kemono_urls.append(u_clean)
            else:
                logger.warning(f"❓ 这个链接好像不认识呢: {url}")
                row["Status"] = "failed"
        
        # 批量处理 Pixiv
        if pixiv_urls:
            logger.info(f"🎨 正在批量处理 {len(pixiv_urls)} 个 Pixiv 链接...")
            try:
                # 调用 process_url，它现在支持列表，并假设列表中的都是作品链接
                # 注意：process_url 返回的是总体统计，无法精确得知每个 URL 的成功失败状态
                # 但我们需要更新 CSV。
                # 由于 process_url 内部是多线程且返回的是统计，我们暂时无法从返回值反推每个 URL 的状态。
                # 
                # 为了解决这个问题，我们需要修改 process_url 或者在这里改变策略。
                # 鉴于 process_url 已经不仅是下载，还包含了重试逻辑等，
                # 且 process_url 内部会打印详细日志。
                # 
                # 为了保持 CSV 状态同步，我们可以利用 process_url 的副作用：
                # 它会将成功的 URL 加入到 self.existing_sources (内存集合)。
                # 我们可以检查 process_url 执行后，哪些 URL 在 existing_sources 里了。
                
                # 先记录一下当前的 existing_sources
                # 但 PixivDownloader 实例是懒加载的，这里先获取一下确保加载
                _ = self.pixiv 
                
                # 执行批量下载
                self.pixiv.process_url(pixiv_urls)
                
                # 检查状态
                for u in pixiv_urls:
                    # 检查是否在已下载清单中（process_url 成功后会加入内存中的 existing_sources）
                    if self.pixiv._is_source_in_manifest(u):
                        status = "done"
                    else:
                        # 如果不在清单里，可能是失败了，也可能是被跳过了
                        # 暂时标记为 failed，用户可以再次 pull 重试
                        status = "failed"
                    
                    # 更新对应的所有行
                    if u in url_to_indices:
                        for idx in url_to_indices[u]:
                            rows[idx]["Status"] = status

            except Exception as e:
                logger.error(f"Pixiv 批量处理发生错误: {e}")
                # 标记这批为失败
                for u in pixiv_urls:
                    if u in url_to_indices:
                        for idx in url_to_indices[u]:
                            rows[idx]["Status"] = "failed"

        # 批量处理 Kemono
        if kemono_urls:
            logger.info(f"🦁 正在批量处理 {len(kemono_urls)} 个 Kemono 链接...")
            try:
                _ = self.kemono
                self.kemono.process_url(kemono_urls)
                
                for u in kemono_urls:
                    if self.kemono._is_source_in_manifest(u):
                        status = "done"
                    else:
                        status = "failed"
                    
                    if u in url_to_indices:
                        for idx in url_to_indices[u]:
                            rows[idx]["Status"] = status
                            
            except Exception as e:
                logger.error(f"Kemono 批量处理发生错误: {e}")
                for u in kemono_urls:
                    if u in url_to_indices:
                        for idx in url_to_indices[u]:
                            rows[idx]["Status"] = "failed"

        # 保存更新后的列表
        self._save_list(csv_path, fieldnames, rows)

        # 重新统计结果
        final_success = 0
        final_failed = 0
        for idx in pending_indices:
            s = rows[idx].get("Status")
            if s == "done":
                final_success += 1
            elif s == "failed":
                final_failed += 1

        logger.info("=" * 40)
        logger.info(f"✨ 本次任务完成！成功 {final_success} | 失败 {final_failed}")
        
        return 0 if final_failed == 0 else 1
