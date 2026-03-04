import argparse
import shutil
import os
from pathlib import Path
from typing import Optional, Set

from src.cli.core import BaseCommand
from toolboxs import logger, get_project_root

class CleanCommand(BaseCommand):
    """
    清理命令实现类。
    """
    def __init__(self) -> None:
        super().__init__()
        self.args: Optional[argparse.Namespace] = None

    @property
    def name(self) -> str:
        """
        命令名称：clean
        """
        return "clean"

    @property
    def description(self) -> str:
        """
        命令描述：清理项目中的临时文件、缓存和空目录。
        """
        return "清理项目中的临时文件、缓存和空目录。"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置命令行参数解析器。
        """
        parser.add_argument(
            "-l", "--library",
            action="store_true",
            help="清理库文件。"
        )
        parser.add_argument(
            "-c", "--csv",
            action="store_true",
            help="清理 CSV 文件。"
        )
        parser.add_argument(
            "-e", "--empty-dirs",
            action="store_true",
            help="递归清理空目录。"
        )
        parser.add_argument(
            "-g", "--logs",
            action="store_true",
            help="清理日志文件 (*.log)。"
        )
        parser.add_argument(
            "-a", "--all",
            action="store_true",
            help="清除所有内容（元数据、库、CSV、缓存、空目录、日志）。"
        )
        parser.add_argument(
            "-q", "--query",
            type=Path,
            nargs='?',
            const=Path.cwd(),
            default=Path.cwd(),
            help="指定清理操作的目标路径（默认为当前目录）。"
        )
        parser.add_argument(
            "-f", "--force",
            action="store_true",
            help="强制清理，不提示确认。"
        )

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行清理操作。
        """
        self.args = args
        
        # 如果指定了 --all，则执行全量清理
        if args.all:
            self._clean_all(args.query)
            return 0
        
        # 执行特定的清理操作
        specific_action = False
        
        if args.library:
            self._clean_library()
            specific_action = True
        
        if args.csv:
            self._clean_csv()
            specific_action = True

        if args.logs:
            self._clean_logs(args.query)
            specific_action = True
            
        # 无论是否执行了特定清理，默认都执行一次通用的缓存清理
        self._clean_cache(args.query)

        # 清理空目录通常放在最后执行
        if args.empty_dirs:
            self._clean_empty_dirs(args.query)
            specific_action = True

        return 0

    def _confirm(self, message: str) -> bool:
        """
        请求用户确认。
        """
        if self.args and self.args.force:
            return True
        try:
            response = input(f"{message} [y/N]: ").lower().strip()
            return response == 'y'
        except EOFError:
            return False

    def _clean_library(self) -> None:
        """
        清理库目录。
        """
        # 使用父类已解析的路径
        target_path = self.library_path

        if not target_path.exists():
            logger.warning(f"👻 咦？库目录好像不在这里: {target_path}")
            return

        if self._confirm(f"🔥 真的要清空整个大书库 {target_path} 吗？(里面的书都会消失哦!)"):
            logger.info("🧹 开始大扫除啦...")
            deleted_count = 0
            
            # 遍历并清理内容，保留目录本身
            for item in target_path.iterdir():
                # 跳过元数据目录
                if item.name == ".meta":
                    continue
                    
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                    logger.info(f"🗑️  拜拜了: {item.name}")
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"❌ 删除 {item.name} 失败: {e}")
            
            if deleted_count > 0:
                logger.info(f"✨ 书库变得干干净净啦！共清理了 {deleted_count} 个项目。")
            else:
                logger.info("✨ 书库本来就很干净呢~")
        else:
            logger.info("🚫 清理操作已取消。")

    def _clean_csv(self) -> None: 
        """
        清理生成的 CSV 清单文件与关注列表文件。
        """
        # 使用父类已解析的路径
        target_path = self.csv_path
        follow_path =get_project_root()/"follow.csv"
        if target_path.exists():
            if self._confirm(f"📜 确定要撕毁这张清单 {target_path} 吗?"):
                try:
                    target_path.unlink()
                    follow_path.unlink()
                    logger.info(f"✅ 咔嚓！清单文件与关注列表文件已销毁: {target_path.name}")
                except Exception as e:
                    logger.error(f"❌ 销毁清单文件与关注列表文件失败: {e}")
        else:
            logger.warning(f"🍃 找不到清单文件呢: {target_path.name}")

    def _clean_logs(self, root_path: Path) -> None:
        """
        清理日志文件。
        """
        logger.info("📝 正在检查旧的日志文件...")
        # 默认清理项目根目录下的日志
        project_root = get_project_root()
        targets = ["*.log"]
        
        found_items: Set[Path] = set()
        
        # 收集所有匹配项 (优先检查根目录)
        for pattern in targets:
            try:
                found_items.update(project_root.glob(pattern))
                # 同时也检查当前查询目录（如果是不同目录）
                if root_path != project_root:
                     found_items.update(root_path.glob(pattern))
            except Exception:
                continue

        if not found_items:
            logger.info("✨ 没有发现任何日志文件。")
            return

        if self._confirm(f"🔥 确定要删除这 {len(found_items)} 个日志文件吗？"):
            deleted_count = 0
            for item in found_items:
                try:
                    # 尝试关闭可能占用的 handler (虽然这里是在运行中，可能无法完全关闭，但尝试一下)
                    # 注意：删除正在使用的 application.log 可能会导致报错或无法写入，建议谨慎
                    # 这里简单处理：如果删除失败就跳过
                    item.unlink()
                    logger.info(f"🗑️  已删除日志: {item.name}")
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"❌ 删除日志 {item.name} 失败 (可能正在使用中): {e}")
            
            logger.info(f"✨ 清理了 {deleted_count} 个日志文件。")
        else:
             logger.info("🚫 日志清理已取消。")

    def _clean_empty_dirs(self, root_path: Path) -> None:
        """
        递归清理空目录。
        """
        logger.info(f"📂 正在寻找空荡荡的房间 (在 {root_path})...")
        
        deleted_count = 0
        
        # 自底向上遍历，这样子目录删除后，父目录变空了也能被删除
        for dirpath, dirnames, filenames in os.walk(root_path, topdown=False):
            # 过滤掉隐藏目录和特定目录
            if ".git" in dirpath or ".venv" in dirpath or "__pycache__" in dirpath:
                continue
                
            try:
                p = Path(dirpath)
                # 如果目录为空（os.rmdir 只能删除空目录，所以不需要额外检查 len(os.listdir)）
                # 但为了避免异常，还是简单检查一下
                if not any(p.iterdir()):
                     p.rmdir()
                     logger.info(f"🗑️  拜拜了空房间: {p.relative_to(root_path)}")
                     deleted_count += 1
            except Exception:
                # 忽略删除非空目录的错误，或者权限错误
                pass
        
        if deleted_count > 0:
             logger.info(f"✨ 整理完毕！清理了 {deleted_count} 个空房间。")
        else:
             logger.info("✨ 没有发现空房间呢。")

    def _clean_cache(self, root_path: Path) -> None:
        """
        通用清理：清除缓存文件 (__pycache__, .pyc, .DS_Store 等)。
        """
        targets = ["__pycache__", "*.pyc", "*.pyo", ".DS_Store", ".pytest_cache"]
        logger.info(f"🔍 正在寻找角落里的灰尘 ({', '.join(targets)}) ...")
        
        cleaned_count = 0
        found_items: Set[Path] = set()
        
        # 收集所有匹配项
        for pattern in targets:
            try:
                found_items.update(root_path.rglob(pattern))
            except Exception:
                continue
                
        # 按路径长度降序排序，确保先删除子文件/子目录，再删除父目录
        sorted_items = sorted(list(found_items), key=lambda p: len(p.parts), reverse=True)
        
        for item in sorted_items:
            # 检查文件是否还存在（可能作为子项已被删除）
            if not item.exists():
                continue
                
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                cleaned_count += 1
            except Exception as e:
                logger.warning(f"💦 哎呀，擦不掉 {item.name}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"✨ 呼~ 打扫完毕！清理了 {cleaned_count} 处灰尘。")
        else:
            logger.info("✨ 哇，这里一尘不染呢！")

    def _clean_all(self, query: Path) -> None:
        """
        清理所有内容（元数据、库、CSV、缓存、空目录、日志）。
        """
        logger.warning("🚨 高能预警！正在启动【毁灭模式】全量清理...")
        
        # 如果未强制，则进行一次总确认
        if self.args and not self.args.force:
            if not self._confirm("🔥 ⚠️  即将执行全量清理（库、清单、缓存、日志、空目录），此操作不可逆！确定要继续吗？"):
                logger.info("🚫 全量清理已取消。")
                return
            # 用户确认后，临时开启强制模式以跳过后续子任务的确认
            self.args.force = True
            
        self._clean_library()
        self._clean_csv()
        self._clean_logs(query)
        self._clean_cache(query)
        self._clean_empty_dirs(query)
        logger.info("🌈 世界清静了，全量清理完成！")