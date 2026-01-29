import csv
import argparse
import shutil
import json
import time
from pathlib import Path
from src.cli.core import BaseCommand
from toolboxs import determine_file_type, get_library_path, generate_file_md5, get_project_root,export_library_manifest

class ImportCommand(BaseCommand):
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
        manifest_name = "library_manifest.csv"
        try:
            config_path = root / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                manifest_name = config.get("project_settings", {}).get("csv_path", manifest_name)
        except Exception:
            pass
            
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

    def _supplement_csv(self, metadata: dict):
        """
        补充 CSV 文件中的缺失字段。
        逻辑：
        1. 读取 config.json 中的 project_settings.csv_path。
        2. 如果文件存在，补充刚导入的 JSON 数据。
        3. 如果不存在，运行 export_library_manifest() 导出清单文件。
        """
        try:
            root = get_project_root()
            config_path = root / "config.json"
            manifest_name = "library_manifest.csv"
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                manifest_name = data.get("project_settings", {}).get("csv_path", manifest_name)
        except Exception as e:
            print(f"❌ 读取配置文件失败: {e}")
            return

        csv_path = root / manifest_name
        
        if not csv_path.exists():
            export_library_manifest(str(csv_path))
            print(f"✅ 成功导出清单文件: {csv_path}")
            return

        # 如果文件存在，则追加新记录
        try:
            headers = [
                "文件名", "作者", "系列", "标签", "来源", 
                "后缀", "分类", "导入时间", "文件大小(Bytes)", "MD5", "文件路径"
            ]
            
            # 准备要写入的数据行
            tags = metadata.get("tags", [])
            tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
            
            row_dict = {
                "文件名": metadata.get("original_filename", ""),
                "作者": metadata.get("author", ""),
                "系列": metadata.get("series", ""),
                "标签": tags_str,
                "来源": metadata.get("source", ""),
                "后缀": metadata.get("file_type", ""),
                "分类": metadata.get("type", ""),
                "导入时间": metadata.get("import_time", ""),
                "文件大小(Bytes)": metadata.get("file_size", 0),
                "MD5": metadata.get("md5", ""),
                "文件路径": metadata.get("file_path", "")
            }
            
            # 以追加模式打开 CSV
            with open(csv_path, 'a', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                # 如果文件是空的（理论上不会，因为 exists 检查过了，但以防万一），写表头
                if f.tell() == 0:
                    writer.writeheader()
                writer.writerow(row_dict)
            print(f"✅ 清单文件已更新: {csv_path}")
            
        except Exception as e:
            print(f"❌ 更新 CSV 文件失败: {e}")
        
    def _create_metadata_json(self, json_path: Path, args: argparse.Namespace, source_file: Path, target_file: Path, file_md5: str) -> dict:
        """
        生成元数据 JSON 文件
        """
        metadata = {
            "original_filename": source_file.name,
            "author": args.author if args.author else None,
            "series": args.series if args.series else None,
            "tags": self._parse_tags(args.tags) if args.tags else [],
            "source": args.source if args.source else None,
            "file_type": source_file.suffix[1:],
            "type": determine_file_type(str(source_file)),
            "import_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "file_size": target_file.stat().st_size if target_file.exists() else 0,
            "md5": file_md5,
            "file_path": str(target_file.relative_to(get_project_root()))
        }
        
        try:
            # 确保父目录存在
            json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
            return metadata
        except Exception as e:
            print(f"❌ 元数据生成失败: {e}")
            return None

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
            print(f"⚠️  文件已存在 (MD5 命中): {args.file.name}")
            print(f"   库中已有同内容文件: {dup_name}")
            print("   导入已取消。")
            return 0  # 正常退出，但未执行导入
            
        # 2. 识别文件类型
        file_type = determine_file_type(str(args.file))
        if file_type == "unknown":
            print(f"无法识别文件类型: {args.file}")
            return 1
        type_path=get_library_path() / file_type
        json_path=get_library_path() / ".meta" / file_type
        
        # 使用封装的函数计算并创建存储路径
        current_path = self._determine_storage_path(type_path, args.author, args.series)
        json_folder = self._determine_storage_path(json_path, args.author, args.series)
        
        # 构建 JSON 文件路径 (与源文件同名，但后缀为 .json)
        json_file_path = json_folder / (args.file.stem + ".json")
        
        # 构建目标文件路径
        target_path = current_path / args.file.name
        
        try:
            shutil.copy2(args.file, target_path)
            print(f"✅ 文件已导入: {target_path}")
            
            # 生成元数据 JSON
            metadata = self._create_metadata_json(json_file_path, args, args.file, target_path, file_md5)
            if metadata:
                print(f"✅ json文件已导入: {json_file_path}")
                # 补充到 CSV 清单
                self._supplement_csv(metadata)
        except Exception as e:
            print(f"❌ 导入失败: {e}")
            return 1
            
        return 0



