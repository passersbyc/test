import json
import csv
import shutil
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Set
from types import SimpleNamespace
from tqdm import tqdm

from toolboxs import (
    get_project_root,
    get_library_path,
    clean_filename,
    determine_file_type,
    determine_storage_path,
    build_import_target,
    generate_file_md5,
    create_metadata,
    supplement_csv
)

class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

def setup_logging(log_filename: str = "application.log"):
    log_file = get_project_root() / log_filename
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(str(log_file), encoding='utf-8'),
            TqdmLoggingHandler()
        ]
    )

class BaseDownloader:
    """
    下载器基类，封装了配置加载、清单管理、文件导入等通用逻辑。
    """
    def __init__(self):
        self.existing_sources: Set[str] = set()
        self._last_error = ""
        # 子类应该在初始化时调用 _load_existing_sources 和 _load_config

    def _set_last_error(self, message: str) -> None:
        self._last_error = message or ""
        if message:
            logging.error(message)

    def _clear_last_error(self) -> None:
        self._last_error = ""

    def _get_manifest_path(self) -> Path:
        root = get_project_root()
        manifest_name = "library_manifest.csv"
        config_path = root / "config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                manifest_name = config.get("project_settings", {}).get("csv_path", manifest_name)
            except Exception:
                pass
        path = Path(manifest_name)
        return path if path.is_absolute() else root / path

    def _load_existing_sources(self):
        self.existing_sources.clear()
        csv_path = self._get_manifest_path()
        if not csv_path.exists():
            return
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    src = row.get("来源") or row.get("source")
                    if src:
                        self.existing_sources.add(src.strip())
            # print(f"📚 已加载 {len(self.existing_sources)} 条现有记录")
        except Exception as e:
            print(f"⚠️ 加载现有记录失败: {e}")
            logging.error(f"加载现有记录失败: {e}")

    def _is_source_in_manifest(self, work_url: str) -> bool:
        if not work_url:
            return False
        return work_url.strip() in self.existing_sources

    def import_download(self, file_path: Path, work_url: str, metadata_info: Dict[str, Any]) -> tuple[Optional[Path], str]:
        """
        通用导入逻辑：将下载的文件移动到库目录，生成元数据并更新 CSV。
        
        Args:
            file_path: 下载的临时文件路径
            work_url: 来源 URL
            metadata_info: 包含元数据的字典，应包含:
                - author: 作者名
                - series: 系列名 (可选)
                - title: 标题 (可选，用于重命名)
                - tags: 标签列表或字符串 (可选)
                
        Returns:
            (目标路径, 状态消息)
        """
        if not file_path.exists():
            return None, "下载文件不存在"
        if not work_url:
            return None, "来源 URL 为空"

        author = metadata_info.get("author", "")
        series = metadata_info.get("series", "")
        title = metadata_info.get("title", "")
        tags = metadata_info.get("tags", [])
        
        # 1. 文件名清理与重命名
        # 如果提供了 title，尝试用 title + 后缀作为文件名，否则使用原文件名
        suffix = file_path.suffix
        expected_name = f"{clean_filename(title)}{suffix}" if title else clean_filename(file_path.name)
        
        # 确保文件名不为空
        if not expected_name or expected_name == suffix:
            expected_name = clean_filename(file_path.name)
            
        if file_path.name != expected_name:
            new_path = file_path.with_name(expected_name)
            try:
                file_path.rename(new_path)
                file_path = new_path
            except OSError as e:
                return None, f"重命名文件失败: {e}"

        # 2. 确定文件类型
        file_type = determine_file_type(str(file_path))
        if file_type == "unknown":
            ext = file_path.suffix.lower()
            ext_display = ext[1:] if ext else ""
            config_path = get_project_root() / "config.json"
            return None, f"无法识别文件类型(ext={ext_display or '无'}; config={config_path})"

        # 3. 构建目标路径
        base = get_library_path() / file_type
        # 这一步可能会创建 author/series 目录
        determine_storage_path(base, author, series)
        target_path = build_import_target(file_path, author, series)

        # 4. 移动文件
        try:
            shutil.move(str(file_path), str(target_path))
        except Exception as e:
            return None, f"移动文件失败: {e}"

        # 5. 生成元数据并更新 CSV
        try:
            file_md5 = generate_file_md5(target_path)
            
            tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
            
            args = SimpleNamespace(
                author=author,
                series=series,
                tags=tags_str,
                source=work_url,
            )
            # create_metadata 需要源文件路径，这里我们已经移动了，所以 source 和 target 都是 target_path
            metadata = create_metadata(args, target_path, target_path, file_md5)
            # 补充额外信息（如果 create_metadata 没有覆盖到）
            if title:
                metadata["original_filename"] = expected_name
                
            supplement_csv(metadata)
        except Exception as e:
            # 虽然文件移动成功了，但元数据更新失败，这是一个部分成功
            logging.error(f"更新元数据失败: {e}")
            return target_path, f"导入成功但元数据更新失败: {e}"

        return target_path, "ok"
