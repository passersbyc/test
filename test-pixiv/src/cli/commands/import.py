import argparse
import shutil
import csv
from pathlib import Path
from src.cli.core import BaseCommand
from toolboxs import generate_file_md5, build_import_target, check_duplicate_by_md5, logger

class ImportCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()

    def _check_duplicate(self, file_md5: str) -> tuple[bool, str]:
        return check_duplicate_by_md5(file_md5)

    def _parse_tags(self, tags_str):
        if not tags_str:
            return []
        return [tag.strip() for tag in tags_str.split(',') if tag.strip()]

    def _determine_storage_path(self, base_path: Path, author: str, series: str) -> Path:
        from toolboxs import determine_storage_path
        return determine_storage_path(base_path, author, series)

    @property
    def name(self) -> str:
        """
        命令名称：import
        """
        return "import"

    @property
    def description(self) -> str:
        """
        命令描述：从文件/文件夹导入数据。
        """
        return "导入资源"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置 import 命令的参数。
        
        参数：
        - file: 要导入的文件路径。
        """
        def is_file(path_str):
            path = Path(path_str)
            if not path.exists():
                 raise argparse.ArgumentTypeError(f"文件 '{path_str}' 不存在")
            if not path.is_file():
                raise argparse.ArgumentTypeError(f"'{path_str}' 不是一个有效的文件路径")
            return path
            
        parser.add_argument("file", type=is_file, help="传入要导入的文件路径")
        parser.add_argument("--author","-a", type=str, help="指定 资源的作者")
        parser.add_argument("--series","-s", type=str, help="指定 资源的系列")
        parser.add_argument("--tags","-t", type=str, help="指定 资源的标签，多个标签用逗号分隔")
        parser.add_argument("--source","-o", type=str, help="指定 资源的来源")

    def _supplement_csv(self, metadata: dict):
        from toolboxs import supplement_csv
        return supplement_csv(metadata)
        
    def _create_metadata(self, args: argparse.Namespace, source_file: Path, target_file: Path, file_md5: str) -> dict:
        from toolboxs import create_metadata
        return create_metadata(args, source_file, target_file, file_md5)

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行导入命令。
        :param args: 解析后的命令行参数。
        :return: 整数退出码，0 表示成功，非 0 表示失败。
        """
        # 1. 计算文件 MD5 并进行查重
        logger.info(f"🕵️ 正在鉴定文件: {args.file.name}...")
        file_md5 = generate_file_md5(args.file)
        is_dup, dup_name = check_duplicate_by_md5(file_md5)
        
        if is_dup:
            logger.warning(f"👯‍♀️ 哎呀！发现双胞胎 (MD5 命中): {args.file.name}")
            logger.warning(f"   库里已经有它啦: {dup_name}")
            logger.warning("   为了节省空间，本次导入取消咯。")
            return 0  # 正常退出，但未执行导入
            
        # 2. 构建目标文件路径
        try:
            target_path = build_import_target(args.file, args.author or "", args.series or "")
        except ValueError:
            logger.error(f"🤯 无法识别这个文件的类型: {args.file}")
            return 1
        
        try:
            shutil.copy2(args.file, target_path)
            logger.info(f"✅ 成功收录到书库: {target_path}")
            
            # 生成元数据
            metadata = self._create_metadata(args, args.file, target_path, file_md5)
            if metadata:
                # 补充到 CSV 清单
                self._supplement_csv(metadata)
        except Exception as e:
            logger.error(f"💥 导入过程发生意外: {e}")
            return 1
            
        return 0
