import csv
import argparse
import shutil
import json
import time
from pathlib import Path
from src.cli.core import BaseCommand
from toolboxs import determine_file_type, get_library_path, generate_file_md5, get_project_root, export_library_manifest, supplement_csv, create_metadata

class ImportCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()

    def _check_duplicate(self, file_md5: str) -> tuple[bool, str]:
        """
        检查文件是否已存在。
        逻辑：扫描 library_manifest.csv 中的 MD5 列。
        :return: (是否重复, 重复文件的原始名称)
        """
        if not file_md5:
            return False, ""
            
        root = get_project_root()
        # 尝试从 config.json 获取清单文件名
        manifest_name = self.config.get("project_settings", {}).get("csv_path", "library_manifest.csv")
            
        manifest_path = root / manifest_name
        if not manifest_path.exists():
            return False, ""
            
        try:
            with open(manifest_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("MD5") == file_md5:
                        return True, row.get("文件名", "未知文件")
        except Exception as e:
            print(f"警告: 查重时读取清单失败: {e}")
            
        return False, ""

    def _parse_tags(self, tags_str):
        if not tags_str:
            return []
        return [tag.strip() for tag in tags_str.split(',') if tag.strip()]

    def _determine_storage_path(self, base_path: Path, author: str, series: str) -> Path:
        """
        根据作者和系列计算最终存储路径。
        逻辑:
        1. 基础路径 (library/type)
        2. + 作者 (如果有)
        3. + 系列 (如果有)
        """
        target_path = base_path
        if author and series:
            target_path = target_path / author / series
        elif author and not series:
            target_path = target_path / author
        elif not author and series:
            target_path = target_path / "unsort"
        elif not author and not series:
            pass

        # 统一创建目录
        target_path.mkdir(parents=True, exist_ok=True)
        return target_path

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

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行导入命令。
        :param args: 解析后的命令行参数。
        :return: 整数退出码，0 表示成功，非 0 表示失败。
        """
        # 1. 计算文件 MD5 并进行查重
        print(f"正在扫描文件: {args.file.name}...")
        file_md5 = generate_file_md5(args.file)
        is_dup, dup_name = self._check_duplicate(file_md5)
        
        if is_dup:
            print(f"⚠️ 文件已存在于书库中，跳过导入。")
            print(f"   现有文件: {dup_name}")
            return 0

        # 2. 确定目标路径
        library_dir = get_library_path()
        base_path = library_dir / determine_file_type(str(args.file))
        
        target_dir = self._determine_storage_path(base_path, args.author, args.series)
        target_file = target_dir / args.file.name
        
        # 3. 复制文件
        print(f"正在导入到: {target_file}...")
        try:
            shutil.copy2(args.file, target_file)
        except Exception as e:
            print(f"❌ 复制文件失败: {e}")
            return 1
            
        # 4. 生成元数据并更新 CSV
        tags = self._parse_tags(args.tags) if args.tags else []
        metadata = create_metadata(
            source_file=args.file,
            target_file=target_file,
            file_md5=file_md5,
            author=args.author,
            series=args.series,
            tags=tags,
            source=args.source
        )
        supplement_csv(metadata)
        
        print("✅ 导入完成！")
        return 0



