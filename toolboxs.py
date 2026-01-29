import json
import sys
import hashlib
import csv
import re
import unicodedata
from pathlib import Path

def get_project_root() -> Path:
    """
    动态获取项目根目录。
    无论从哪个脚本调用，都能准确找到 config.json 所在的根目录。
    """
    # __file__ 是当前文件 (toolboxs.py) 的路径
    # 因为 toolboxs.py 就在根目录下，所以它的 parent 就是根目录
    return Path(__file__).parent.absolute()

def get_library_path() -> Path:
    """
    获取书库存储路径。
    优先读取 config.json 中的配置，若无配置则默认返回项目根目录下的 'library' 文件夹。
    """
    root = get_project_root()
    config_path = root / "config.json"
    
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            # 尝试从 project_settings -> library_path 读取
            path_str = config.get("project_settings", {}).get("library_path")
            if path_str:
                path = Path(path_str)
                # 如果是相对路径，则相对于项目根目录解析
                return path if path.is_absolute() else (root / path).absolute()
        except Exception:
            pass
    
    # 默认兜底方案：根目录下的 library 文件夹
    default_path = root / "library"
    default_path.mkdir(exist_ok=True)
    return default_path

def translate_error(message: str) -> str:
    """
    将 argparse 的英文错误信息翻译为中文。
    从 config.json 读取翻译配置。
    """
    translations = {}
    config_path = Path.cwd() / "config.json"
    
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            translations = config.get("translations", {})
        except Exception as e:
            print(f"警告: 无法读取配置文件 {config_path}: {e}", file=sys.stderr)

    translated = message
    for eng, chn in translations.items():
        translated = translated.replace(eng, chn)
    return translated

def determine_file_type(file_path: str) -> str:
    """
    根据文件扩展名确定文件类型。
    从 config.json 读取文件类型映射配置。
    :param file_path: 文件路径
    """
    path_obj = Path(file_path)
    ext = path_obj.suffix.lower()
    
    if ext:
        ext_key = ext[1:]  # 去掉点号，例如 ".txt" -> "txt"
    else:
        return "unknown"
    
    filetype_mapping = {}
    config_path = Path.cwd() / "config.json"
    
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            filetype_mapping = config.get("filetype", {})
        except Exception as e:
            print(f"警告: 无法读取配置文件 {config_path}: {e}", file=sys.stderr)
    
    return filetype_mapping.get(ext_key, "unknown")

def generate_file_md5(file_path: Path, chunk_size: int = 8192) -> str:
    """
    生成文件的 MD5 哈希值。
    使用流式读取，即使是大文件也不会占用过多内存。
    :param file_path: 文件路径
    :param chunk_size: 每次读取的块大小（默认 8KB）
    :return: 32位 MD5 字符串
    """
    md5_hash = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            # 循环读取文件内容并更新哈希对象
            for chunk in iter(lambda: f.read(chunk_size), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except Exception as e:
        print(f"错误: 无法计算文件 MD5 {file_path}: {e}", file=sys.stderr)
        return ""

def clean_filename(filename: str, replace_char: str = "_") -> str:
    """
    清洗文件名。
    1. 将全角字符转换为半角字符 (NFKC 规范化)。
    2. 如果全角字符转换后变成了 Windows 非法字符 (< > : " / \\ | ? *)，则进行替换。
    3. 保留原始的半角非法字符（不处理）。
    4. 去除首尾空白字符和不可见字符。

    :param filename: 原始文件名
    :param replace_char: 用于替换非法字符的字符串，默认为下划线
    :return: 清洗后的文件名
    """
    if not filename:
        return ""

    illegal_chars = set('<>:"/\\|?*')
    result = []
    
    for char in filename:
        # 如果原本就是半角非法字符，直接保留
        if char in illegal_chars:
            result.append(char)
            continue
            
        # 否则尝试规范化
        normalized = unicodedata.normalize('NFKC', char)
        
        # 检查规范化后的字符是否包含非法字符
        clean_segment = ""
        for n_char in normalized:
            if n_char in illegal_chars:
                clean_segment += replace_char
            else:
                clean_segment += n_char
        
        result.append(clean_segment)

    cleaned = "".join(result)

    # 去除不可见字符 (如控制符) 和首尾空格
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned.strip()

def export_library_manifest(output_csv_path: str = None) -> str:
    """
    导出书库清单：递归扫描 library/.meta 目录下的所有 JSON 文件，并汇总生成一个 CSV 文件。
    :param output_csv_path: 输出的 CSV 文件路径
    :return: 生成的 CSV 文件的绝对路径
    """
    if output_csv_path is None:
        output_csv_path = config["project_settings"]["csv_path"]
    
    root = get_project_root()
    meta_dir = root / "library" / ".meta"
    output_path = root / output_csv_path
    
    if not meta_dir.exists():
        return f"错误: 目录 {meta_dir} 不存在"

    # 定义 CSV 表头
    headers = [
        "文件名", "作者", "系列", "标签", "来源", 
        "后缀", "分类", "导入时间", "文件大小(Bytes)", "MD5","文件路径"
    ]
    
    data_rows = []
    
    # 递归查找所有 .json 文件
    for json_file in meta_dir.rglob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                
                # 处理标签列表，转为逗号分隔的字符串
                tags = meta.get("tags", [])
                tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
                
                row = [
                    meta.get("original_filename", ""),
                    meta.get("author", ""),
                    meta.get("series", ""),
                    tags_str,
                    meta.get("source", ""),
                    meta.get("file_type", ""),
                    meta.get("type", ""),
                    meta.get("import_time", ""),
                    meta.get("file_size", 0),
                    meta.get("md5", ""),  # 获取 MD5 字段
                    meta.get("file_path", "")  # 获取 文件路径 字段
                ]
                data_rows.append(row)
        except Exception as e:
            print(f"警告: 无法处理元数据文件 {json_file}: {e}", file=sys.stderr)

    # 写入 CSV 文件
    try:
        with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(data_rows)
        return str(output_path.absolute())
    except Exception as e:
        return f"错误: 无法写入 CSV 文件: {e}"