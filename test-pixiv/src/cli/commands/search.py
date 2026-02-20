import csv
import argparse
from pathlib import Path
from src.cli.core import BaseCommand
from toolboxs import determine_file_type, get_library_path, generate_file_md5, get_project_root,export_library_manifest

class SearchCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__()
    
    @property
    def description(self) -> str:
        """
        命令描述：搜索库中的文件，根据文件名或文件内容进行匹配，并返回ID。
        """
        return "搜索库中的文件，根据文件名或文件内容进行匹配，并返回ID。"

    @property
    def name(self) -> str:
        """
        命令名称：search
        """
        return "search"

    def _search_author(self, query: str) -> list[dict]:
        """
        搜索作者名下的作品。
        :param query: 搜索的作者名称（支持模糊匹配）。
        :return: 返回包含该作者所有作品信息的字典列表。
        """
        results = []
        try:
            with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 获取作者列，如果为空则设为""
                    author = row.get("作者", "")
                    if not author:
                        continue
                        
                    # 模糊匹配：只要 query 在作者名中出现即可 (不区分大小写)
                    if query.lower() in author.lower():
                        results.append(row)
        except Exception as e:
            print(f"❌ 读取清单失败: {e}")
            
        return results

    def _search_series(self, query: str) -> list[dict]:
        """
        搜索系列名下的作品。
        :param query: 搜索的系列名称（支持模糊匹配）。
        :return: 返回包含该系列所有作品信息的字典列表。
        """
        results = []
        try:
            with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 获取系列列，如果为空则设为""
                    series = row.get("系列", "")
                    if not series:
                        continue
                        
                    # 模糊匹配：只要 query 在系列名中出现即可 (不区分大小写)
                    if query.lower() in series.lower():
                        results.append(row)
        except Exception as e:
            print(f"❌ 读取清单失败: {e}")
            
        return results

    def _search_type(self, query: str) -> list[dict]:
        """
        搜索指定文件类型的作品。
        :param query: 搜索的文件类型（支持模糊匹配）。
        :return: 返回包含该类型所有作品信息的字典列表。
        """
        results = []
        try:
            with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 获取文件类型列，如果为空则设为""
                    file_type = row.get("文件类型", "")
                    if not file_type:
                        continue
                        
                    # 模糊匹配：只要 query 在文件类型中出现即可 (不区分大小写)
                    if query.lower() in file_type.lower():
                        results.append(row)
        except Exception as e:
            print(f"❌ 读取清单失败: {e}")
            
        return results

    def _search_file(self, query: str) -> list[dict]:
        """
        搜索文件名或文件内容。
        :param query: 搜索的文件名或文件内容（支持模糊匹配）。
        :return: 返回包含匹配作品信息的字典列表。
        """
        results = []
        try:
            with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 获取文件名列，如果为空则设为""
                    file_name = row.get("文件名", "")
                    if not file_name:
                        continue
                        
                    # 模糊匹配：只要 query 在文件名中出现即可 (不区分大小写)
                    if query.lower() in file_name.lower():
                        results.append(row)
        except Exception as e:
            print(f"❌ 读取清单失败: {e}")
            
        return results

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        为当前命令配置参数解析器。
        在这里添加该命令所需的特定参数（如 --name, -v 等）。
        
        Args:
            parser: 为该命令创建的子解析器实例。
        """
        self.add_arguments(parser)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """
        添加命令行参数。
        """
        parser.add_argument("query", type=str, nargs="?", help="搜索的文件名（如果使用了 -a/-s/-t 筛选参数，此项可省略）")
        parser.add_argument("-a","--author", type=str, help="作者名称，用于筛选文件")
        parser.add_argument("-s","--series", type=str, help="系列名称，用于筛选文件")
        parser.add_argument("-t","--type", type=str, help="文件类型，用于筛选文件")


    def execute(self, args: argparse.Namespace) -> int:
        """
        执行搜索操作。
        """
        # 检查 CSV 路径是否存在
        if not self.csv_path.exists():
            print(f"❌ 错误：CSV 路径不存在: {self.csv_path}")
            return 1
            
        results = []
        
        # 根据参数选择搜索模式
        if args.author:
            print(f"🔍 正在搜索作者包含 '{args.author}' 的作品...")
            results = self._search_author(args.author)
        elif args.series:
            print(f"🔍 正在搜索系列包含 '{args.series}' 的作品...")
            results = self._search_series(args.series)
        elif args.type:
            print(f"🔍 正在搜索类型包含 '{args.type}' 的作品...")
            results = self._search_type(args.type)
        else:
            if not args.query:
                print("❌ 错误：请提供搜索关键词，或使用 -a/-s/-t 指定筛选条件。")
                return 1
            print(f"🔍 正在搜索文件名包含 '{args.query}' 的作品...")
            results = self._search_file(args.query)
            
        # 输出结果
        if not results:
            print("📭 未找到匹配的结果。")
            return 0
            
        print(f"🎉 找到 {len(results)} 个匹配结果：")
        print("-" * 85)
        # 打印表头 - 调整宽度
        # ID: 6 chars, 文件名: 40 chars, 作者: 20 chars, 大小: 12 chars
        print(f"{'ID':<6} {'文件名':<40} {'作者':<20} {'大小(KB)':<12}")
        print("-" * 85)
        
        id_list = []
        for row in results:
            file_id = row.get('ID', 'N/A')
            if file_id != 'N/A':
                id_list.append(file_id)
            
            # 截断过长的文件名以保持对齐 (中文占2字符宽度，这里简单处理，实际应计算宽字符)
            # 增加显示长度
            name = row.get('文件名', '')
            if len(name) > 38:
                name = name[:35] + "..."
                
            author = row.get('作者', '未知')
            if len(author) > 18:
                author = author[:15] + "..."
                
            size = row.get('文件大小(KB)', '0')
            
            print(f"{file_id:<6} {name:<40} {author:<20} {size:<12}")
        
        print("-" * 85)
        # 打印ID列表摘要
        if id_list:
            print(f"📋 ID 列表: {', '.join(id_list)}")
            
        return 0