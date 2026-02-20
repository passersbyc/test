from src.cli.core import BaseCommand
from pathlib import Path
import json
import shutil
import argparse
import csv
from toolboxs import get_project_root, remove_empty_directories

class DeleteCommand(BaseCommand):
    """
    删除命令实现类。
    """
    def __init__(self) -> None:
        super().__init__()
        self.args = None  # 用于存储解析后的参数
        self.data = self.config
    
    @property
    def name(self) -> str:
        """
        命令名称：delete
        """
        return "delete"

    @property
    def description(self) -> str:
        """
        命令描述：删除指定文件。
        """
        return "删除指定文件。"

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        配置命令行参数解析器。
        """
        parser.add_argument(
            "query",
            type=str,
            nargs='+',
            help="要删除的文件名ID列表 (用空格分隔)，或者作者/系列/类型的名称。"
        )
        parser.add_argument(
            "-a","--author",
            action="store_true",
            help="按作者删除文件。"
        )
        parser.add_argument(
            "-t","--type",
            action="store_true",
            help="按类型删除文件。"
        )
        parser.add_argument(
            "-s","--series",
            action="store_true",
            help="按系列删除文件。"
        )
        parser.add_argument(
            "-f","--force",
            action="store_true",
            help="强制删除，不提示确认。"
        )


    def execute(self, args: argparse.Namespace) -> int:
        """
        执行删除操作。
        """
        self.args = args
        # 对于作者/系列/类型，将参数列表拼接为字符串（支持带空格的名字）
        query_str = " ".join(args.query)
        
        if args.author:
            self._delete_author(query_str)
        elif args.type:
            self._delete_type(query_str)
        elif args.series:
            self._delete_series(query_str)
        else:
            # 默认按 ID 删除，支持逗号分隔或空格分隔
            ids = []
            for q in args.query:
                # 兼容 1,2,3 这种格式
                parts = q.split(',')
                for p in parts:
                    clean_id = p.strip()
                    if clean_id:
                        ids.append(clean_id)
            self._delete_file(ids)
            # 删除空目录
        remove_empty_directories(self.library_path)
        return 0

    def _delete_author(self, query: str) -> None:
        """
        删除指定作者的所有文件。
        """
        author_path = self.library_path / query
        if author_path.exists():
            if self.args.force or self._confirm(f"❓ 确定要删除作者 {query} 及其所有作品吗?"):
                try:
                    shutil.rmtree(author_path)
                    print(f"✅ 已删除作者: {query}")
                    # 这里可能需要更新 CSV，但全量更新比较慢，建议提示用户手动 scan
                    print("💡 提示: 建议运行 'scan' 命令更新书库清单。")
                except Exception as e:
                    print(f"❌ 删除失败: {e}")
        else:
            print(f"✨ 作者不存在: {query}")

    def _delete_type(self, query: str) -> None:
        """
        删除指定类型的所有文件。
        """
        type_path = self.library_path / query
        if type_path.exists():
            if self.args.force or self._confirm(f"❓ 确定要删除类型 {query} 下的所有文件吗?"):
                try:
                    shutil.rmtree(type_path)
                    print(f"✅ 已删除类型: {query}")
                    print("💡 提示: 建议运行 'scan' 命令更新书库清单。")
                except Exception as e:
                    print(f"❌ 删除失败: {e}")
        else:
            print(f"✨ 类型不存在: {query}")
    
    def _delete_series(self, query: str) -> None:
        """
        删除指定系列的所有文件。
        """
        series_path = self.library_path / query
        # 系列通常位于 作者目录下，但这里假设 query 是相对 library 的路径？
        # 不，系列通常是 library/<type>/<author>/<series> 或者 library/unsort/<author>/<series>
        # 这里的实现比较简单，假设系列直接在 library 下？或者用户需要提供相对路径？
        # 之前的代码是 self.data["project_settings"]["library_path"] / query
        # 这意味着用户必须提供相对于 library 的路径，或者系列文件夹就在 library 根目录下（不太可能）。
        # 但既然是重构，先保持原有逻辑（虽然原有逻辑可能只对 library 根目录下的文件夹有效）。
        # 实际上系列一般在作者下面。搜索功能也只能搜到名字。
        # 如果要删除系列，可能需要遍历查找。
        # 鉴于此，先按原逻辑修复路径拼接问题。
        
        series_path = self.library_path / query
        if series_path.exists():
            if self.args.force or self._confirm(f"❓ 确定要删除系列 {query} 及其所有文件吗?"):
                try:
                    shutil.rmtree(series_path)
                    print(f"✅ 已删除系列: {query}")
                    print("💡 提示: 建议运行 'scan' 命令更新书库清单。")
                except Exception as e:
                    print(f"❌ 删除失败: {e}")
        else:
            # 尝试在所有作者目录下查找该系列
            found = False
            for author_dir in self.library_path.iterdir():
                if author_dir.is_dir():
                    possible_series = author_dir / query
                    if possible_series.exists() and possible_series.is_dir():
                        if self.args.force or self._confirm(f"❓ 找到系列 '{query}' 位于 '{author_dir.name}' 下。确定要删除吗?"):
                            try:
                                shutil.rmtree(possible_series)
                                print(f"✅ 已删除系列: {possible_series}")
                                found = True
                            except Exception as e:
                                print(f"❌ 删除失败: {e}")
                        else:
                            found = True # Found but user cancelled
                        break # Only delete first match or logic gets complex
            
            if not found:
                print(f"✨ 系列不存在: {query}")
    
    def _delete_file(self, ids: list[str]) -> None:
        """
        通过ID列表，删除指定文件，并更新 CSV 清单。
        """
        # 1. 读取 CSV
        rows = []
        try:
            with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception:
            print("❌ 读取清单失败，请检查 CSV 文件。")
            return

        # 2. 查找要删除的文件
        ids_set = set(str(i) for i in ids)
        remaining_rows = []
        files_to_delete = []

        # 保留未被选中的行，收集被选中的行
        for row in rows:
            if row.get("ID") in ids_set:
                files_to_delete.append(row)
            else:
                remaining_rows.append(row)

        if not files_to_delete:
            print("📭 未找到对应的 ID。")
            return

        # 3. 删除文件并确认
        deleted_count = 0
        root = get_project_root()
        
        # 用于记录真正删除成功的行，只有删除成功（或文件本身不存在）才从 CSV 移除
        final_remaining_rows = list(remaining_rows)

        # 打印预览
        print(f"🔍 找到 {len(files_to_delete)} 个文件待删除:")
        for row in files_to_delete:
             print(f"  - [ID: {row.get('ID')}] {row.get('文件名')} ({row.get('文件大小(KB)')} KB)")

        if not self.args.force and not self._confirm("❓ 确定要删除以上所有文件吗?"):
            print("🚫 已取消删除。")
            return

        for row in files_to_delete:
            file_path_str = row.get("文件路径")
            if not file_path_str:
                continue
            
            full_path = root / file_path_str
            
            if not full_path.exists():
                print(f"⚠️ 文件不存在 (仅从清单移除): {full_path.name}")
                deleted_count += 1
                continue

            try:
                full_path.unlink()
                print(f"✅ 已删除文件: {full_path.name}")
                deleted_count += 1
            except Exception as e:
                print(f"❌ 删除失败: {full_path.name} - {e}")
                # 删除失败则保留在清单中
                final_remaining_rows.append(row)

        # 4. 更新 CSV
        if deleted_count > 0:
            # 使用原表头或 toolboxs 定义的标准表头
            headers = [
                "ID", "文件名", "作者", "系列", "标签", "来源", 
                "后缀", "分类", "导入时间", "文件大小(KB)", "MD5", "文件路径"
            ]
            
            try:
                with open(self.csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=headers)
                    writer.writeheader()
                    # 为了保持 ID 顺序，可能需要重排序？不，保持原序即可，或者按 ID 排序
                    # 这里直接写入剩余行
                    writer.writerows(final_remaining_rows)
                print("📋 清单已更新。")
            except Exception as e:
                print(f"❌ 更新清单失败: {e}")
