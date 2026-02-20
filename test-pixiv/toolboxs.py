import json
import sys
import hashlib
import csv
import re
import unicodedata
import zipfile
import shutil
from pathlib import Path
import html

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
    config_path = get_project_root() / "config.json"
    
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
    config_path = get_project_root() / "config.json"
    
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            filetype_mapping = config.get("filetype", {})
        except Exception as e:
            print(f"警告: 无法读取配置文件 {config_path}: {e}", file=sys.stderr)
    
    return filetype_mapping.get(ext_key, "unknown")

def build_import_target(file: Path, author: str = "", series: str = "") -> Path:
    """
    根据文件、作者、系列构建最终存储目标路径。
    逻辑与 import 命令一致：
    1) 识别文件类型 -> library/<type>
    2) 路径拼接：有作者且有系列 -> /author/series；仅作者 -> /author；仅系列 -> /unsort；都无 -> 不追加
    3) 创建目录并返回目标文件完整路径
    """
    file_type = determine_file_type(str(file))
    if file_type == "unknown":
        raise ValueError(f"无法识别文件类型: {file}")
    base = get_library_path() / file_type
    a = author.strip() if author else ""
    s = series.strip() if series else ""
    if a and s:
        base = base / clean_filename(a) / clean_filename(s)
    elif a and not s:
        base = base / clean_filename(a)
    elif not a and s:
        base = base / "unsort"
    base.mkdir(parents=True, exist_ok=True)
    return base / file.name

def determine_storage_path(base_path: Path, author: str = "", series: str = "") -> Path:
    a = author.strip() if author else ""
    s = series.strip() if series else ""
    target = base_path
    if a and s:
        target = target / clean_filename(a) / clean_filename(s)
    elif a and not s:
        target = target / clean_filename(a)
    elif not a and s:
        target = target / "unsort"
    target.mkdir(parents=True, exist_ok=True)
    return target

def check_duplicate_by_md5(file_md5: str, manifest_name: str = None) -> tuple[bool, str]:
    """
    根据 MD5 在清单中查重。
    :param file_md5: 文件 MD5
    :param manifest_name: 清单文件名（可选），默认从 config.json 的 project_settings.csv_path 读取
    :return: (是否重复, 重复文件的原始名称)
    """
    if not file_md5:
        return False, ""
    root = get_project_root()
    if manifest_name is None:
        try:
            config_path = root / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                manifest_name = config.get("project_settings", {}).get("csv_path", "library_manifest.csv")
            else:
                manifest_name = "library_manifest.csv"
        except Exception:
            manifest_name = "library_manifest.csv"
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
        print(f"警告: 查重时读取清单失败: {e}", file=sys.stderr)
    return False, ""

def supplement_csv(metadata: dict) -> None:
    try:
        root = get_project_root()
        config_path = root / "config.json"
        manifest_name = "library_manifest.csv"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                manifest_name = config.get("project_settings", {}).get("csv_path", "library_manifest.csv")
            except Exception:
                pass
    except Exception as e:
        print(f"❌ 读取配置失败: {e}")
        return
    csv_path = root / manifest_name
    if not csv_path.exists():
        print(f"⚠️ 清单文件不存在，正在创建: {csv_path}")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        headers = [
            "ID", "文件名", "作者", "系列", "标签", "来源",
            "后缀", "分类", "导入时间", "文件大小(KB)", "MD5", "文件路径"
        ]
        new_id = generate_next_id(csv_path)
        tags = metadata.get("tags", [])
        tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
        row_dict = {
            "ID": new_id,
            "文件名": metadata.get("original_filename", ""),
            "作者": metadata.get("author", ""),
            "系列": metadata.get("series", ""),
            "标签": tags_str,
            "来源": metadata.get("source", ""),
            "后缀": metadata.get("file_type", ""),
            "分类": metadata.get("type", ""),
            "导入时间": metadata.get("import_time", ""),
            "文件大小(KB)": metadata.get("file_size", 0),
            "MD5": metadata.get("md5", ""),
            "文件路径": metadata.get("file_path", "")
        }
        with open(csv_path, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(row_dict)
        print(f"✅ 清单文件已更新: {csv_path} (ID: {new_id})")
    except Exception as e:
        print(f"❌ 更新 CSV 文件失败: {e}")

def create_metadata(args, source_file: Path, target_file: Path, file_md5: str) -> dict:
    import time
    author = args.author if getattr(args, "author", None) else None
    series = args.series if getattr(args, "series", None) else None
    tags_raw = args.tags if getattr(args, "tags", None) else ""
    tags = [tag.strip() for tag in tags_raw.split(',') if tag.strip()] if tags_raw else []
    metadata = {
        "original_filename": source_file.name,
        "author": author,
        "series": series,
        "tags": tags,
        "source": args.source if getattr(args, "source", None) else None,
        "file_type": source_file.suffix[1:] if source_file.suffix else "",
        "type": determine_file_type(str(source_file)),
        "import_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "file_size": round(target_file.stat().st_size / 1024, 2) if target_file.exists() else 0,
        "md5": file_md5,
        "file_path": str(target_file.relative_to(get_project_root())) if target_file.exists() else str(target_file)
    }
    return metadata

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
    mapping = {
        '<': '＜',
        '>': '＞',
        ':': '：',
        '"': '＂',
        '/': '／',
        '\\': '＼',
        '|': '｜',
        '?': '？',
        '*': '＊',
    }
    result = []
    
    for char in filename:
        if char in illegal_chars:
            result.append(mapping.get(char, replace_char))
            continue
            
        normalized = unicodedata.normalize('NFKC', char)
        
        clean_segment = ""
        for n_char in normalized:
            if n_char in illegal_chars:
                clean_segment += mapping.get(n_char, replace_char)
            else:
                clean_segment += n_char
        
        result.append(clean_segment)

    cleaned = "".join(result)

    # 去除不可见字符 (如控制符) 和首尾空格
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned.strip()

def description_to_text(text: str) -> str:
    """
    将包含 HTML 的简介文本转换为纯文本：
    1) 解码 HTML 实体
    2) 将 <br> / </p> 等换行标签转换为换行
    3) 移除 script/style 及其他 HTML 标签
    4) 规范空白与换行
    """
    if not text:
        return ""
    s = html.unescape(text)
    s = re.sub(r'(?i)<br\\s*/?>', '\n', s)
    s = re.sub(r'(?i)</p\\s*>', '\n', s)
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\\1>', '', s)
    s = re.sub(r'(?s)<[^>]+>', '', s)
    s = re.sub(r'\\r\\n?', '\n', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    s = '\n'.join(line.strip() for line in s.splitlines())
    return s.strip()

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

def convert_images_to_book(folder_path: Path, target_format: str = 'pdf', delete_original: bool = True) -> Path:
    """
    将文件夹内的图片合并为 PDF 或 CBZ 文件。
    
    :param folder_path: 图片文件夹路径 (Path 对象)
    :param target_format: 目标格式，'pdf' 或 'cbz' (不区分大小写)
    :param delete_original: 是否在转换成功后删除原文件夹，默认为 True
    :return: 生成的文件路径
    """
    if not folder_path.exists() or not folder_path.is_dir():
        raise ValueError(f"路径不存在或不是文件夹: {folder_path}")

    target_format = target_format.lower()
    if target_format not in ['pdf', 'cbz']:
        raise ValueError(f"不支持的格式: {target_format}。仅支持 'pdf' 或 'cbz'")

    # 支持的图片扩展名
    image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
    
    # 获取并排序图片
    # 使用自然排序 (1, 2, 10 而不是 1, 10, 2)
    def natural_sort_key(path):
        return [int(text) if text.isdigit() else text.lower()
                for text in re.split(r'(\d+)', path.name)]

    images = [
        p for p in folder_path.iterdir() 
        if p.is_file() and p.suffix.lower() in image_extensions and not p.name.startswith('.')
    ]
    images.sort(key=natural_sort_key)

    if not images:
        raise ValueError(f"在 {folder_path} 中未找到支持的图片文件")

    # 使用父目录 + 文件夹名 + 后缀，防止文件夹名包含点号导致的解析错误
    output_path = folder_path.parent / (folder_path.name + f'.{target_format}')
    
    try:
        if target_format == 'pdf':
            # 懒加载导入 PIL，避免未安装时的硬崩溃
            try:
                from PIL import Image
            except ImportError:
                raise ImportError("生成 PDF 需要安装 Pillow 库。请运行: pip install Pillow")

            # 打开第一张图片并转换为 RGB (PDF 不支持 RGBA)
            first_image = Image.open(images[0])
            if first_image.mode != 'RGB':
                first_image = first_image.convert('RGB')
                
            other_images = []
            for img_path in images[1:]:
                img = Image.open(img_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                other_images.append(img)
                
            first_image.save(
                output_path, 
                "PDF", 
                resolution=100.0, 
                save_all=True, 
                append_images=other_images
            )
            
        elif target_format == 'cbz':
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for img_path in images:
                    # 在 zip 中只存储文件名，不存储绝对路径
                    zf.write(img_path, arcname=img_path.name)
        
        print(f"成功生成: {output_path}")

        if delete_original:
            try:
                shutil.rmtree(folder_path)
                print(f"已删除原文件夹: {folder_path}")
            except Exception as e:
                print(f"警告: 删除原文件夹失败: {e}", file=sys.stderr)
                
        return output_path

    except Exception as e:
        # 如果生成失败，清理可能生成的半成品
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"转换失败: {e}")
