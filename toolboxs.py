import json
import sys
import hashlib
import csv
import re
import unicodedata
import zipfile
import shutil
from pathlib import Path

def get_project_root() -> Path:
    """
    动态获取项目根目录。
    无论从哪个脚本调用，都能准确找到 config.json 所在的根目录。
    
    :return: 项目根目录的 Path 对象
    """
    # __file__ 是当前文件 (toolboxs.py) 的路径
    # 因为 toolboxs.py 就在根目录下，所以它的 parent 就是根目录
    return Path(__file__).parent.absolute()

def get_library_path() -> Path:
    """
    获取书库存储路径。
    优先读取 config.json 中的配置，若无配置则默认返回项目根目录下的 'library' 文件夹。
    
    :return: 书库目录的 Path 对象
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
    
    :param message: 原始英文错误信息
    :return: 翻译后的中文错误信息，若无匹配则返回原信息
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
    
    :param file_path: 文件路径字符串
    :return: 文件类型字符串 (如 'image', 'book')，未知类型返回 'unknown'
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

def html_to_text(html_content: str) -> str:
    """
    将 HTML 内容转换为纯文本。
    1. 将 <br> 和 <p> 转换为换行符。
    2. 去除所有 HTML 标签。
    3. 处理常见的 HTML 实体 (如 &lt;, &gt;, &nbsp; 等)。
    
    :param html_content: 原始 HTML 字符串
    :return: 清洗后的纯文本
    """
    if not html_content:
        return ""

    # 1. 替换换行标签
    # <br>, <br/>, <br /> -> \n
    text = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
    # </p> -> \n
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    
    # 2. 去除所有 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    
    # 3. 处理 HTML 实体
    # 仅处理最常见的几个，如果需要更完整的处理可以使用 html.unescape
    import html
    text = html.unescape(text)
    
    return text.strip()

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

def supplement_csv(metadata: dict):
    """
    补充 CSV 文件中的缺失字段。
    逻辑：
    1. 读取 config.json 中的 project_settings.csv_path。
    2. 如果文件存在，补充刚导入的 JSON 数据。
    3. 如果不存在，运行 export_library_manifest() 导出清单文件。
    """
    try:
        root = get_project_root()
        # 尝试读取配置
        manifest_name = "library_manifest.csv"
        try:
            config_path = root / "config.json"
            if config_path.exists():
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
        # 创建父目录（如果不存在）
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        # 初始化一个空文件，稍后会写入表头和数据
        # 这里不需要显式写入表头，因为下面的逻辑会在文件为空时自动写入表头
        pass

    # 如果文件存在，则追加新记录
    try:
        headers = [
            "ID", "文件名", "作者", "系列", "标签", "来源", 
            "后缀", "分类", "导入时间", "文件大小(KB)", "MD5", "文件路径"
        ]
        
        # 生成新的 ID
        new_id = generate_next_id(csv_path)
        
        # 准备要写入的数据行
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
        
        # 以追加模式打开 CSV
        with open(csv_path, 'a', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            # 如果文件是空的（理论上不会，因为 exists 检查过了，但以防万一），写表头
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(row_dict)
        print(f"✅ 清单文件已更新: {csv_path} (ID: {new_id})")
        
    except Exception as e:
        print(f"❌ 更新 CSV 文件失败: {e}")

def create_metadata(source_file: Path, target_file: Path, file_md5: str, author: str = None, series: str = None, tags: list = None, source: str = None) -> dict:
    """
    生成标准化的元数据字典，用于后续写入 CSV 清单。
    
    :param source_file: 原始文件路径 (Path 对象)，用于获取原始文件名。
    :param target_file: 目标文件路径 (Path 对象)，即文件存入书库后的实际路径，用于计算文件大小和相对路径。
    :param file_md5: 文件的 MD5 哈希值，用于唯一标识和查重。
    :param author: (可选) 作品作者。
    :param series: (可选) 作品所属系列。
    :param tags: (可选) 标签列表，如 ["pixiv", "original"]。
    :param source: (可选) 来源信息，如 URL 或 来源平台名称。
    
    :return: 包含所有元数据字段的字典，可直接传给 supplement_csv 函数。
             字典键包括:
             - original_filename: 原始文件名
             - author: 作者
             - series: 系列
             - tags: 标签列表
             - source: 来源
             - file_type: 文件后缀 (不含点)
             - type: 文件分类 (根据后缀判断，如 image, book)
             - import_time: 导入时间 (YYYY-MM-DD HH:MM:SS)
             - file_size: 文件大小 (KB)
             - md5: 文件 MD5
             - file_path: 相对于项目根目录的文件路径
    """
    import time
    metadata = {
        "original_filename": source_file.name,
        "author": author,
        "series": series,
        "tags": tags if tags else [],
        "source": source,
        "file_type": source_file.suffix[1:],
        "type": determine_file_type(str(source_file)),
        "import_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "file_size": round(target_file.stat().st_size / 1024, 2) if target_file.exists() else 0,
        "md5": file_md5,
        "file_path": str(target_file.relative_to(get_project_root()))
    }
    return metadata
