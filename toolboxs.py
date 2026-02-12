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

def generate_next_id(csv_path: Path = None) -> int:
    """
    生成下一个可用的数字 ID。
    逻辑：读取 CSV 清单中最大的 ID 值，然后 +1。
    如果 CSV 不存在或为空，则从 1 开始。
    
    :param csv_path: CSV 清单文件路径，如果不传则自动查找
    :return: 下一个 ID (整数)
    """
    if csv_path is None:
        root = get_project_root()
        # 尝试读取配置
        try:
            config_path = root / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                path_str = config.get("project_settings", {}).get("csv_path", "library_manifest.csv")
                csv_path = root / path_str
            else:
                csv_path = root / "library_manifest.csv"
        except Exception:
            csv_path = root / "library_manifest.csv"
            
    if not csv_path.exists():
        return 1
        
    max_id = 0
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # 假设 ID 列名为 "ID"
                    current_id = int(row.get("ID", 0))
                    if current_id > max_id:
                        max_id = current_id
                except (ValueError, TypeError):
                    continue
    except Exception:
        pass
        
    return max_id + 1

def export_library_manifest(output_csv_path: str = None) -> str:

    """
    导出书库清单：扫描 library 目录下的所有文件，根据路径信息生成 CSV 清单。
    由于不再使用 .json 元数据文件，本函数通过解析文件路径来推断部分元数据。
    路径结构假设: library/<type>/<author>/<series>/<filename>
    
    :param output_csv_path: 输出的 CSV 文件路径
    :return: 生成的 CSV 文件的绝对路径
    """
    import time
    
    root = get_project_root()
    
    # 尝试读取配置获取默认路径
    if output_csv_path is None:
        try:
            config_path = root / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                output_csv_path = config.get("project_settings", {}).get("csv_path", "library_manifest.csv")
            else:
                output_csv_path = "library_manifest.csv"
        except Exception:
            output_csv_path = "library_manifest.csv"

    library_dir = get_library_path()
    output_path = root / output_csv_path
    
    if not library_dir.exists():
        return f"错误: 书库目录 {library_dir} 不存在"

    # 定义 CSV 表头
    headers = [
        "ID", "文件名", "作者", "系列", "标签", "来源", 
        "后缀", "分类", "导入时间", "文件大小(KB)", "MD5", "文件路径"
    ]
    
    data_rows = []
    
    # 获取现有 CSV 中的 ID 映射 (path -> id)
    # 这样重建清单时可以保持原有文件的 ID 不变
    existing_ids = {}
    next_id_counter = 1
    
    if output_path.exists():
        try:
            with open(output_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    path = row.get("文件路径")
                    pid = row.get("ID")
                    if path and pid:
                        try:
                            existing_ids[path] = int(pid)
                            if int(pid) >= next_id_counter:
                                next_id_counter = int(pid) + 1
                        except ValueError:
                            pass
        except Exception:
            pass
            
    # 递归查找所有文件
    # 排除 .DS_Store, library_manifest.csv 以及隐藏文件
    for file_path in library_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.name.startswith("."):
            continue
        if file_path.name == Path(output_csv_path).name:
            continue
            
        try:
            # 计算相对于 library 目录的路径
            rel_path = file_path.relative_to(library_dir)
            parts = rel_path.parts
            
            # 初始化字段
            file_type_category = "unknown"
            author = ""
            series = ""
            
            # 根据目录深度推断元数据
            # 假设结构: type/author/series/filename
            if len(parts) >= 1:
                file_type_category = parts[0] # 第一层通常是分类 (book, image, etc.)
            
            if len(parts) >= 2:
                # 第二层可能是作者，也可能是 unsort
                if parts[1] != "unsort":
                    author = parts[1]
            
            if len(parts) >= 3:
                # 第三层可能是系列，也可能是文件名
                # 只有当后面还有文件名时，这层才算系列
                if len(parts) > 3:
                     series = parts[2]
            
            # 获取基本文件信息
            stat = file_path.stat()
            file_size = round(stat.st_size / 1024, 2)
            import_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
            md5 = generate_file_md5(file_path)
            
            # 标签和来源无法从路径恢复，留空
            tags_str = ""
            source = ""
            
            # 确定具体后缀
            suffix = file_path.suffix[1:] if file_path.suffix else ""
            
            # 确定 ID
            rel_root_path = str(file_path.relative_to(root))
            if rel_root_path in existing_ids:
                file_id = existing_ids[rel_root_path]
            else:
                file_id = next_id_counter
                next_id_counter += 1
            
            row = [
                file_id,
                file_path.name,
                author,
                series,
                tags_str,
                source,
                suffix,
                file_type_category,
                import_time,
                file_size,
                md5,
                rel_root_path
            ]
            data_rows.append(row)
            
        except Exception as e:
            print(f"警告: 无法处理文件 {file_path}: {e}", file=sys.stderr)

    # 写入 CSV 文件
    try:
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 按 ID 排序
        data_rows.sort(key=lambda x: x[0])
        
        with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(data_rows)
        return str(output_path.absolute())
    except Exception as e:
        return f"错误: 无法写入 CSV 文件: {e}"

def remove_empty_directories(directory: Path = None) -> int:
    """
    递归删除指定目录下的空文件夹。
    如果不指定目录，默认使用 get_library_path() 获取的书库目录。
    
    :param directory: 要清理的目录路径，默认为书库路径
    :return: 删除的空文件夹数量
    """
    if directory is None:
        directory = get_library_path()
        
    if not directory.exists():
        return 0
        
    deleted_count = 0
    
    # 获取所有子目录
    # rglob('*') 会递归查找，is_dir() 筛选目录
    # 按路径部分数量降序排序，确保先处理最深层的子目录（模拟 bottom-up）
    # 这样当子目录被删除后，父目录变空也能被删除
    all_subdirs = sorted(
        [p for p in directory.rglob('*') if p.is_dir()],
        key=lambda p: len(p.parts),
        reverse=True
    )
    
    for path in all_subdirs:
        try:
            path.rmdir()
            deleted_count += 1
        except OSError:
            # 目录非空或无权限，跳过
            pass
            
    return deleted_count

