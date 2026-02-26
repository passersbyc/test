import csv
import argparse
import re
import unicodedata
from src.cli.core import BaseCommand
from toolboxs import update_library_manifest

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

    def _read_rows(self) -> list[dict]:
        try:
            with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
                return list(csv.DictReader(f))
        except Exception as e:
            print(f"❌ 读取清单失败: {e}")
            return []

    def _build_matcher(self, pattern: str, use_regex: bool):
        if not pattern:
            return None
        if use_regex:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"正则表达式错误: {e}")
            return lambda value: bool(regex.search(value or ""))
        lowered = pattern.lower()
        return lambda value: lowered in (value or "").lower()

    def _match_any(self, matcher, values: list[str]) -> bool:
        if matcher is None:
            return False
        for v in values:
            if matcher(v):
                return True
        return False

    def _text_width(self, text: str) -> int:
        if text is None:
            return 0
        width = 0
        for ch in str(text):
            width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        return width

    def _truncate(self, text: str, max_width: int) -> str:
        if text is None:
            return ""
        text = str(text)
        if self._text_width(text) <= max_width:
            return text
        if max_width <= 3:
            return text[:max_width]
        target = max_width - 3
        buf = []
        w = 0
        for ch in text:
            cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            if w + cw > target:
                break
            buf.append(ch)
            w += cw
        return "".join(buf) + "..."

    def _pad(self, text: str, width: int) -> str:
        text = "" if text is None else str(text)
        diff = width - self._text_width(text)
        if diff <= 0:
            return text
        return text + (" " * diff)

    def _format_row(self, values: list[str], widths: list[int]) -> str:
        parts = []
        for v, w in zip(values, widths):
            parts.append(self._pad(v, w))
        return " ".join(parts)

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
        parser.add_argument("query", type=str, nargs="?", help="搜索的文件名关键词")
        parser.add_argument("-a","--author", type=str, help="作者名称，用于筛选文件")
        parser.add_argument("-s","--series", type=str, help="系列名称，用于筛选文件")
        parser.add_argument("-t","--type", type=str, help="分类名称，用于筛选文件")
        parser.add_argument("-g","--tag", type=str, help="标签关键词，用于筛选文件")
        parser.add_argument("-o","--source", type=str, help="来源关键词，用于筛选文件")
        parser.add_argument("-k","--keyword", type=str, help="在文件名/作者/系列/标签/来源中搜索关键词")
        parser.add_argument("--regex", action="store_true", help="使用正则表达式匹配")
        parser.add_argument("--refresh", action="store_true", help="搜索前先更新清单")
        parser.add_argument("--limit", type=int, default=0, help="限制输出数量")


    def execute(self, args: argparse.Namespace) -> int:
        """
        执行搜索操作。
        """
        if args.refresh:
            print("🧹 正在刷新清单，请稍等一下下～")
            update_library_manifest()

        if not self.csv_path.exists():
            update_library_manifest()
            if not self.csv_path.exists():
                print(f"❌ 错误：CSV 路径不存在: {self.csv_path}")
                return 1
            
        if not any([args.query, args.author, args.series, args.type, args.tag, args.source, args.keyword]):
            print("❌ 需要一点关键词线索喔～请输入搜索关键词或筛选条件。")
            return 1

        try:
            matcher_query = self._build_matcher(args.query, args.regex)
            matcher_author = self._build_matcher(args.author, args.regex)
            matcher_series = self._build_matcher(args.series, args.regex)
            matcher_type = self._build_matcher(args.type, args.regex)
            matcher_tag = self._build_matcher(args.tag, args.regex)
            matcher_source = self._build_matcher(args.source, args.regex)
            matcher_keyword = self._build_matcher(args.keyword, args.regex)
        except ValueError as e:
            print(f"❌ {e}")
            return 1

        results = []
        rows = self._read_rows()
        for row in rows:
            file_name = row.get("文件名", "")
            author = row.get("作者", "")
            series = row.get("系列", "")
            tags = row.get("标签", "")
            source = row.get("来源", "")
            file_type = row.get("分类", "") or row.get("文件类型", "") or row.get("类型", "")

            if matcher_query and not matcher_query(file_name):
                continue
            if matcher_author and not matcher_author(author):
                continue
            if matcher_series and not matcher_series(series):
                continue
            if matcher_tag and not matcher_tag(tags):
                continue
            if matcher_source and not matcher_source(source):
                continue
            if matcher_type and not matcher_type(file_type):
                continue
            if matcher_keyword and not self._match_any(matcher_keyword, [file_name, author, series, tags, source]):
                continue

            results.append(row)
            if args.limit > 0 and len(results) >= args.limit:
                break
            
        # 输出结果
        if not results:
            print("📭 呜呜，没有找到匹配结果～")
            return 0
            
        widths = [6, 40, 18, 10, 12]
        line_len = sum(widths) + (len(widths) - 1)
        line = "─" * line_len
        print(f"✨ 找到 {len(results)} 个小宝藏：")
        print(f"╭{line}╮")
        header = self._format_row(["ID", "文件名", "作者", "分类", "大小(KB)"], widths)
        print(f"│{header}│")
        print(f"├{line}┤")
        
        id_list = []
        for row in results:
            file_id = row.get('ID', 'N/A')
            if file_id != 'N/A':
                id_list.append(file_id)
            
            name = row.get('文件名', '')
            name = self._truncate(name, widths[1])
            author = row.get('作者', '未知')
            author = self._truncate(author, widths[2])
                
            size = row.get('文件大小(KB)', '0')
            file_type = row.get("分类", "") or row.get("文件类型", "") or row.get("类型", "")
            file_type = self._truncate(file_type, widths[3])
            
            row_text = self._format_row([str(file_id), name, author, file_type, str(size)], widths)
            print(f"│{row_text}│")
        
        print(f"╰{line}╯")
        if id_list:
            print(f"🧾 可用 ID：{', '.join(id_list)}")
        print("🐾 搜索完成，贴贴～")
            
        return 0
