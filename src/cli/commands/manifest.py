import argparse
import json
from pathlib import Path
from src.cli.core import BaseCommand
from toolboxs import get_library_path, export_library_manifest, get_project_root

class ManifestCommand(BaseCommand):
    """
    导出书库清单命令实现类。
    """
    
    @property
    def name(self) -> str:
        """
        命令名称
        """
        return "manifest"

    @property
    def description(self) -> str:
        """
        命令描述
        """
        return "导出书库清单"
        
    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置命令行参数解析器。
        """
        # 尝试从 config.json 获取默认路径
        default_csv = "library_manifest.csv"
        try:
            config_path = get_project_root() / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                default_csv = config.get("project_settings", {}).get("csv_path", default_csv)
        except Exception:
            pass

        parser.add_argument(
            "output", 
            type=str, 
            nargs="?",
            default=default_csv,
            help=f"指定输出的 CSV 文件路径（默认：{default_csv}）"
        )

    def execute(self, args: argparse.Namespace) -> int:
        """
        执行命令。
        """
        output_path = Path(args.output)
        if not output_path.is_absolute():
            if not Path(output_path).exists():

                result = export_library_manifest(args.output)
        
                if result.startswith("错误"):
                    print(f"❌ {result}")
                    return 1
                else:
                    print(f"✅ 清单已成功生成！路径：{output_path}")
                    return 0
            
                

        else:
            pass
