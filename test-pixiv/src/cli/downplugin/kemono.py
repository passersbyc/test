import urllib.request
import urllib.error
import ssl
import gzip
import json
import re
import logging
import time
import sys
import csv
import shutil
import threading
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from tqdm import tqdm
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))
from toolboxs import clean_filename, description_to_text, convert_images_to_book, get_project_root, determine_file_type, get_library_path, determine_storage_path, build_import_target, generate_file_md5, create_metadata, supplement_csv

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

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler("kemono.log", encoding='utf-8'),
            TqdmLoggingHandler()
        ]
    )

@dataclass
class KemonoConfig:
    base_url: str = "https://kemono.cr/"
    default_image_server: str = "https://n1.kemono.cr"
    image_servers: list = field(default_factory=lambda: [
        "https://n1.kemono.cr",
        "https://n2.kemono.cr",
        "https://n3.kemono.cr",
        "https://n4.kemono.cr",
    ])
    headers: dict = field(default_factory=lambda: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/css"
    })
    timeout: int = 30
    max_retries: int = 3
    default_download_path: str = "test_downloads"
    if_download_cover: bool = False
    image_extensions: set = field(default_factory=lambda: {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'})
    format: str = "pdf"
    max_workers: int = 50

def _normalize_extensions(value):
    if not value:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return None
    result = set()
    for ext in value:
        if not ext:
            continue
        s = str(ext).lower()
        if not s.startswith("."):
            s = "." + s
        result.add(s)
    return result or None

def load_kemono_config() -> KemonoConfig:
    cfg = KemonoConfig()
    root = get_project_root()
    config_path = root / "config.json"
    data = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    kemono_cfg = data.get("kemono_config", {})
    download_cfg = data.get("download", {})
    download_file_path = data.get("download_file_path")
    if kemono_cfg.get("base_url"):
        cfg.base_url = str(kemono_cfg.get("base_url")).rstrip("/") + "/"
    if kemono_cfg.get("default_image_server"):
        cfg.default_image_server = str(kemono_cfg.get("default_image_server")).rstrip("/")
    servers = kemono_cfg.get("image_servers")
    if isinstance(servers, list) and servers:
        cfg.image_servers = [str(s).rstrip("/") for s in servers if str(s).strip()]
    if cfg.default_image_server and cfg.default_image_server not in cfg.image_servers:
        cfg.image_servers.insert(0, cfg.default_image_server)
    headers = kemono_cfg.get("headers")
    if isinstance(headers, dict):
        merged = dict(cfg.headers)
        merged.update(headers)
        cfg.headers = merged
    cookie = data.get("cookie", {}).get("kemono")
    if cookie and "Cookie" not in cfg.headers:
        cfg.headers = dict(cfg.headers)
        cfg.headers["Cookie"] = cookie
    timeout = kemono_cfg.get("timeout", download_cfg.get("timeout"))
    if isinstance(timeout, (int, float)) and timeout > 0:
        cfg.timeout = int(timeout)
    max_workers = kemono_cfg.get("max_workers", download_cfg.get("max_workers"))
    if isinstance(max_workers, int) and max_workers > 0:
        cfg.max_workers = max_workers
    max_retries = kemono_cfg.get("max_retries")
    if isinstance(max_retries, int) and max_retries >= 0:
        cfg.max_retries = max_retries
    if isinstance(download_file_path, str) and download_file_path.strip():
        cfg.default_download_path = download_file_path.strip()
    if isinstance(kemono_cfg.get("if_download_cover"), bool):
        cfg.if_download_cover = kemono_cfg.get("if_download_cover")
    extensions = _normalize_extensions(kemono_cfg.get("image_extensions"))
    if extensions:
        cfg.image_extensions = extensions
    if kemono_cfg.get("format"):
        cfg.format = str(kemono_cfg.get("format")).lower()
    return cfg

# 配置日志
setup_logging()
logger = logging.getLogger(__name__)

class Kemono:
    """
    Kemono 站点的爬虫类，用于获取作者帖子及相关信息。
    """
    def __init__(self, base_url=None):
        """
        初始化爬虫配置
        :param base_url: 站点的根域名
        """
        self.config = load_kemono_config()
        self.url = base_url if base_url else self.config.base_url
        self.headers = self.config.headers

    def _image_servers(self, primary: str | None = None) -> list:
        servers = []
        if primary:
            servers.append(primary.rstrip("/"))
        for s in self.config.image_servers:
            s = str(s).rstrip("/")
            if s and s not in servers:
                servers.append(s)
        if self.config.default_image_server and self.config.default_image_server not in servers:
            servers.append(self.config.default_image_server)
        return servers

    def _build_image_url(self, img_path: str, server: str) -> str:
        server = server.rstrip("/")
        if img_path.startswith("http"):
            if "/data/" in img_path:
                rest = img_path.split("/data/", 1)[1]
                return f"{server}/data/{rest}"
            return img_path
        if not img_path.startswith("/"):
            img_path = "/" + img_path
        if "/data/" in img_path:
            return f"{server}{img_path}"
        return f"{server}/data{img_path}"

    def _resolve_download_path(self, downloads_path):
        if not downloads_path:
            downloads_path = self.config.default_download_path
        base = Path(downloads_path)
        if not base.is_absolute():
            base = get_project_root() / base
        return base
    
    def get_author_artline(self, author_id_or_url, service=None):
        """
        获取指定作者主页的所有帖子链接列表
        :param author_id_or_url: 作者的 ID (例如: 94927303) 或完整的主页 URL
        :param service: 如果传入的是 ID，可以指定服务类型 (默认: patreon)
        :return: 包含所有帖子完整 URL 的列表
        """
        # 尝试从 URL 解析 service 和 author_id
        # 常见格式: https://kemono.cr/patreon/user/125941488
        match = re.search(r'/([^/]+)/user/([^/]+)', author_id_or_url)
        if match:
            service = match.group(1)
            author_id = match.group(2)
        else:
            author_id = author_id_or_url
            if not service:
                service = "patreon" # 默认回落

        seen = set()  # 用于记录已抓取的链接，防止重复
        result = []   # 存储最终的链接结果
        offset = 0    # 分页偏移量
        
        # logger.info(f"开始获取作者帖子列表: {author_id} ({service})")

        while True:
            # 构造 API 请求地址，Kemono 的分页步长通常为 50
            api = f"{self.url}api/v1/{service}/user/{author_id}/posts?o={offset}"
            
            headers = dict(self.headers)
            # 关键：设置 Accept 为 text/css 是该站点目前的绕过策略
            headers["Accept"] = "text/css"
            
            req = urllib.request.Request(api, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    raw_data = resp.read()
                    if resp.info().get('Content-Encoding') == 'gzip' or raw_data.startswith(b'\x1f\x8b'):
                        data = gzip.decompress(raw_data).decode("utf-8", "ignore")
                    else:
                        data = raw_data.decode("utf-8", "ignore")
            except urllib.error.HTTPError as e:
                # 忽略 400 Bad Request，通常是因为 offset 超出范围
                if e.code == 400:
                    break
                    
                body = ""
                try:
                    body = e.read().decode("utf-8", "ignore")
                except Exception:
                    body = ""
                logger.warning(f"抓取中断或结束 (Offset {offset}) HTTP {e.code}: {e.reason} {body[:200]}")
                break
            except Exception as e:
                logger.warning(f"抓取中断或结束 (Offset {offset}): {e}")
                break
                
            try:
                # 解析返回的 JSON 数据
                items = json.loads(data)
            except Exception:
                logger.error(f"解析 JSON 失败: {data[:200]}")
                break
                
            # 如果当前页面没有帖子，说明已经抓取完毕
            if not items:
                break
                
            new_count = 0
            for it in items:
                pid = str(it.get("id"))
                if not pid:
                    continue
                
                # 拼接成完整的帖子访问链接
                link = f"{self.url}{service}/user/{author_id}/post/{pid}"
                
                # 去重检查
                if link not in seen:
                    seen.add(link)
                    result.append(link)
                    new_count += 1
            
            # 如果这一页没有发现任何新帖子，说明已经抓取到末尾
            if new_count == 0:
                break
                
            # 增加偏移量，继续请求下一页
            offset += 50
            
        # logger.info(f"获取完成，共找到 {len(result)} 个帖子")
        return result

    def get_author_name(self, author_id_or_url, service=None):
        """
        获取指定作者的名称
        :param author_id_or_url: 作者的 ID 或完整的主页 URL
        :param service: 如果传入的是 ID，可以指定服务类型
        :return: 作者名称 (如果获取失败返回 None)
        """
        # 尝试从 URL 解析 service 和 author_id
        match = re.search(r'/([^/]+)/user/([^/]+)', author_id_or_url)
        if match:
            service = match.group(1)
            author_id = match.group(2)
        else:
            author_id = author_id_or_url
            if not service:
                service = "patreon" # 默认回落

        api_url = f"{self.url}api/v1/{service}/user/{author_id}/profile"
        headers = dict(self.headers)
        headers["Accept"] = "text/css" # 保持一致的绕过策略

        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                raw_data = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip' or raw_data.startswith(b'\x1f\x8b'):
                    data = gzip.decompress(raw_data).decode("utf-8", "ignore")
                else:
                    data = raw_data.decode("utf-8", "ignore")
                
                profile = json.loads(data)
                name = profile.get("name")
                # logger.info(f"获取到作者名称: {name}")
                return name
        except Exception as e:
            logger.error(f"获取作者名称失败: {e}")
            return None

    def get_author_info(self, url: str) -> Optional[tuple[str, int]]:
        """
        判断是否为作者主页，如果是则返回 (作者名, 作品数)。
        """
        # 常见格式: https://kemono.cr/patreon/user/125941488
        if "/user/" not in url or "/post/" in url:
            return None
            
        name = self.get_author_name(url)
        if not name:
            return None
            
        posts = self.get_author_artline(url)
        return name, len(posts)

    def get_post_downloads(self, post_url, downloads_path=None, if_download_cover=None):
        """
        获取指定帖子的详细内容并下载附件
        :param post_url: 帖子的完整 URL (例如: https://kemono.cr/patreon/user/94927303/post/126798243)
        :param downloads_path: 下载保存路径，默认为当前目录
        :param if_download_cover: 是否下载封面 (Main File)
        """
        # 1. 从 URL 中解析 service, user_id, post_id
        # 常见格式: .../patreon/user/12345/post/67890
        match = re.search(r'/([^/]+)/user/([^/]+)/post/([^/]+)', post_url)
        if not match:
            logger.error(f"无法从 URL 解析帖子信息: {post_url}")
            return

        service, user_id, post_id = match.groups()
        
        # 2. 构造 API 请求
        api_url = f"{self.url}api/v1/{service}/user/{user_id}/post/{post_id}"
        logger.debug(f"正在获取帖子信息: {api_url}")

        headers = dict(self.headers)
        headers["Accept"] = "text/css"
        
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                raw_data = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip' or raw_data.startswith(b'\x1f\x8b'):
                    data = gzip.decompress(raw_data).decode("utf-8", "ignore")
                else:
                    data = raw_data.decode("utf-8", "ignore")
                
                post_data = json.loads(data)
        except Exception as e:
            logger.error(f"获取帖子详情失败 - {e}")
            return

        if if_download_cover is None:
            if_download_cover = self.config.if_download_cover

        # 3. 准备下载目录
        downloads_path = self._resolve_download_path(downloads_path)
        
        if not downloads_path.exists():
            downloads_path.mkdir(parents=True, exist_ok=True)

        # 4. 提取并下载文件
        # Kemono 的附件信息通常分散在 'post' 对象内部的 'file' 和 'attachments'，
        # 以及根目录下的 'attachments' (包含 server 信息)
        
        # 收集所有待下载文件信息
        files_to_download = []
        
        # 辅助字典：通过路径查找对应的 server
        path_to_server = {}
        if 'attachments' in post_data:
             for att in post_data['attachments']:
                 if 'path' in att and 'server' in att:
                     path_to_server[att['path']] = att['server']

        post_info = post_data.get('post', {})
        
        # 获取主文件路径用于去重
        file_path_to_exclude = None
        if 'file' in post_info and post_info['file'] and 'path' in post_info['file']:
            file_path_to_exclude = post_info['file']['path']

        seen_paths = set()
        candidates = []
        if post_info.get("attachments"):
            candidates.extend(post_info.get("attachments", []))
        if post_data.get("attachments"):
            candidates.extend(post_data.get("attachments", []))
        main_file = post_info.get("file")
        if main_file:
            candidates.append(main_file)

        for att in candidates:
            if not isinstance(att, dict):
                continue
            att_path = att.get("path")
            if not att_path:
                continue
            if att_path in seen_paths:
                continue
            if file_path_to_exclude and att_path == file_path_to_exclude and not if_download_cover:
                ext = Path(att_path).suffix.lower()
                if ext in self.config.image_extensions:
                    continue
            if not if_download_cover:
                ext = Path(att_path).suffix.lower()
                if ext in self.config.image_extensions:
                    continue
            seen_paths.add(att_path)
            files_to_download.append(att)

        logger.debug(f"帖子 {post_id}: 找到 {len(files_to_download)} 个文件，准备下载...")
        downloaded_files = []

        for file_info in files_to_download:
            file_path = file_info.get('path')
            file_name = file_info.get('name')
            
            if not file_path:
                continue
                
            # 确定下载链接
            # 如果我们之前找到了 server 信息，就用它；否则默认尝试 n1-n4 
            # 这里的 server 查找可能不是百分百准确，通常 server 是根据 hash 动态分配的
            # 但 post_data['attachments'] 里通常有正确的 server
            
            server = path_to_server.get(file_path)
            if not server:
                server = self.config.default_image_server
            
            # 清理文件名，防止非法字符
            if not file_name:
                file_name = f"{Path(file_path).name}"
                
            safe_name = clean_filename(file_name)
            save_path = downloads_path / safe_name
            temp_path = downloads_path / f"{safe_name}.tmp"
            
            if save_path.exists():
                logger.debug(f"文件已存在，跳过: {safe_name}")
                downloaded_files.append(save_path)
                continue
                
            logger.debug(f"正在下载: {safe_name} ...")
            servers = self._image_servers(server)
            attempts = max(1, self.config.max_retries)
            success = False
            for idx, srv in enumerate(servers):
                download_url = self._build_image_url(file_path, srv)
                for attempt in range(attempts):
                    try:
                        dl_req = urllib.request.Request(download_url, headers=self.headers)
                        with urllib.request.urlopen(dl_req, timeout=60) as dl_resp, open(temp_path, 'wb') as f_out:
                            while True:
                                chunk = dl_resp.read(8192)
                                if not chunk:
                                    break
                                f_out.write(chunk)
                        temp_path.rename(save_path)
                        logger.debug(f"下载完成: {safe_name}")
                        downloaded_files.append(save_path)
                        success = True
                        break
                    except urllib.error.HTTPError as e:
                        if e.code in {502, 503, 504, 429} and attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        logger.error(f"下载失败 {safe_name}: {e}")
                        break
                    except (ssl.SSLError, TimeoutError, urllib.error.URLError) as e:
                        if attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        logger.error(f"下载失败 {safe_name}: {e}")
                    except Exception as e:
                        if attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        logger.error(f"下载失败 {safe_name}: {e}")
                if success:
                    break
                if not success and idx < len(servers) - 1:
                    logger.warning(f"切换到备用图片服务器: {servers[idx + 1]}")
            if not success and temp_path.exists():
                temp_path.unlink()
        return downloaded_files

    def get_post_content(self, post_url, downloads_path=None):
        """
        获取指定帖子的详细内容并保存为txt文件
        :param post_url: 帖子的完整 URL
        :param downloads_path: 下载路径
        """
        # 1. 从 URL 中解析 service, user_id, post_id
        match = re.search(r'/([^/]+)/user/([^/]+)/post/([^/]+)', post_url)
        if not match:
            logger.error(f"无法从 URL 解析帖子信息: {post_url}")
            return None

        service, user_id, post_id = match.groups()
        
        # 2. 构造 API 请求
        api_url = f"{self.url}api/v1/{service}/user/{user_id}/post/{post_id}"
        
        headers = dict(self.headers)
        headers["Accept"] = "text/css"

        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                raw_data = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip' or raw_data.startswith(b'\x1f\x8b'):
                    data = gzip.decompress(raw_data).decode("utf-8", "ignore")
                else:
                    data = raw_data.decode("utf-8", "ignore")
                
                post_data = json.loads(data)
        except Exception as e:
            logger.error(f"获取帖子详情失败 - {e}")
            return

        post_info = post_data.get('post', {})
        title = post_info.get('title', 'Untitled')
        content = post_info.get('content', '')
        
        if not content:
            logger.warning(f"帖子 {title} 没有文本内容")
            # 即使没有内容，也可以创建一个空文件或者直接返回，这里选择返回
            return
            
        content = description_to_text(content)

        # 3. 准备保存目录
        downloads_path = self._resolve_download_path(downloads_path)
            
        if not downloads_path.exists():
            downloads_path.mkdir(parents=True, exist_ok=True)
            
        # 4. 保存文件
        # 清理文件名
        safe_title = clean_filename(title)
        # 限制文件名长度，避免过长报错
        if len(safe_title) > 200:
            safe_title = safe_title[:200]
            
        file_path = downloads_path / f"{safe_title}.txt"
        temp_path = downloads_path / f"{safe_title}.txt.tmp"
        
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(content)
            temp_path.rename(file_path)
            logger.debug(f"内容已保存至: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"保存内容失败: {e}")
            if temp_path.exists():
                temp_path.unlink()
        return None

    def get_post_files(self, post_url, downloads_path=None, format=None):
        """
        获取帖子中的Files内容并下载
        :param post_url: 帖子的完整 URL
        :param downloads_path: 下载保存路径
        :param format: 输出格式 (pdf, cbz, image)
        """
        # 1. 解析帖子信息
        match = re.search(r'/([^/]+)/user/([^/]+)/post/([^/]+)', post_url)
        if not match:
            logger.error(f"无法从 URL 解析帖子信息: {post_url}")
            return None
            
        service, user_id, post_id = match.groups()
        api_url = f"{self.url}api/v1/{service}/user/{user_id}/post/{post_id}"
        
        headers = dict(self.headers)
        headers["Accept"] = "text/css"
        
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                raw_data = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip' or raw_data.startswith(b'\x1f\x8b'):
                    data = gzip.decompress(raw_data).decode("utf-8", "ignore")
                else:
                    data = raw_data.decode("utf-8", "ignore")
                post_data = json.loads(data)
        except Exception as e:
            logger.error(f"获取帖子详情失败 - {e}")
            return None

        post_info = post_data.get('post', {})
        title = post_info.get('title', 'Untitled')
        content = post_info.get('content', '')
        
        # 2. 收集图片 URL
        image_urls = []
        
        # (C) 附件中的图片 (Attachments)
        # 很多 Fanbox/Fantia 帖子将图片放在 attachments 中
        if 'attachments' in post_info and post_info['attachments']:
            for att in post_info['attachments']:
                if 'path' in att:
                    # 检查是否为图片 (如果需要合并 PDF/CBZ)
                    # 或者如果 format 是 image，可能所有文件都想要？
                    # 这里为了统一行为，暂时只把图片加到 image_urls 列表用于处理
                    # 如果需要下载非图片附件，那是 get_post_downloads_content 的职责
                    ext = Path(att['path']).suffix.lower()
                    if ext in self.config.image_extensions:
                        if att['path'] not in image_urls:
                            image_urls.append(att['path'])
                    
        if not image_urls:
            logger.warning(f"帖子 {title} 中未找到图片文件")
            return None
            
        
        # 3. 准备临时下载目录
        safe_title = clean_filename(title)
        if len(safe_title) > 200:
            safe_title = safe_title[:200]
            
        # 如果格式是 image，直接下载到目标目录；否则下载到临时目录
        downloads_path = self._resolve_download_path(downloads_path)
        
        target_dir = downloads_path / safe_title
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            
        downloaded_files = []
        
        # 4. 下载所有图片
        servers = self._image_servers(self.config.default_image_server)
        for i, img_path in enumerate(image_urls):
            # 生成文件名 (保持顺序)
            ext = Path(img_path).suffix
            if not ext:
                ext = ".jpg" # 默认后缀
            
            # 使用序号命名以保证合并顺序
            file_name = f"{i+1:03d}{ext}"
            save_path = target_dir / file_name
            temp_path = target_dir / f"{file_name}.tmp"
            
            if save_path.exists():
                downloaded_files.append(save_path)
                continue
                
            attempts = max(1, self.config.max_retries)
            success = False
            for idx, srv in enumerate(servers):
                dl_url = self._build_image_url(img_path, srv)
                for attempt in range(attempts):
                    try:
                        logger.debug(f"正在下载图片 {i+1}/{len(image_urls)} ...")
                        req = urllib.request.Request(dl_url, headers=self.headers)
                        with urllib.request.urlopen(req, timeout=self.config.timeout) as dl_resp, open(temp_path, 'wb') as f:
                            f.write(dl_resp.read())
                        temp_path.rename(save_path)
                        downloaded_files.append(save_path)
                        success = True
                        break
                    except urllib.error.HTTPError as e:
                        if e.code in {502, 503, 504, 429} and attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        logger.error(f"下载图片失败 {dl_url}: {e}")
                        break
                    except (ssl.SSLError, TimeoutError, urllib.error.URLError) as e:
                        if attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        logger.error(f"下载图片失败 {dl_url}: {e}")
                    except Exception as e:
                        if attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        logger.error(f"下载图片失败 {dl_url}: {e}")
                if success:
                    break
                if not success and idx < len(servers) - 1:
                    logger.warning(f"切换到备用图片服务器: {servers[idx + 1]}")
            if not success and temp_path.exists():
                temp_path.unlink()

        if not downloaded_files:
            logger.warning("没有图片下载成功")
            # 清理空目录
            if not any(target_dir.iterdir()):
                target_dir.rmdir()
            return None

        # 5. 根据格式处理
        if format is None:
            format = self.config.format
        if format.lower() == "image":
            logger.info(f"所有图片已下载至文件夹: {target_dir}")
            return downloaded_files
            
        elif format.lower() in {"pdf", "cbz"}:
            try:
                output_file = convert_images_to_book(target_dir, target_format=format.lower(), delete_original=True)
                logger.debug(f"已生成: {output_file}")
                return output_file
            except Exception as e:
                logger.error(f"生成 {format.upper()} 失败: {e}")
        else:
            logger.warning(f"不支持的格式: {format}，仅保留下载的图片文件夹")
        return None

    def get_author_content(self, author_url: str, format: str, downloads_path=None):
        """
        批量获取作者的所有帖子内容 (多线程版)
        :param author_url: 作者主页链接
        :param format: 下载类型 (downloads, content, files)
        """
        url_lines = self.get_author_artline(author_url)
        total_posts = len(url_lines)
        logger.info(f"共找到 {total_posts} 个帖子，准备使用多线程处理...")
        
        author_name = self.get_author_name(author_url)
        if not author_name:
             # 如果获取不到名字，尝试从 URL 提取 ID 作为备选
             match = re.search(r'/user/([^/]+)', author_url)
             author_name = match.group(1) if match else "Unknown_Author"

        downloads_path = self._resolve_download_path(downloads_path)
        download_path = downloads_path / clean_filename(author_name)

        # 定义单任务处理函数
        def process_post(post_url):
            retries = self.config.max_retries
            for attempt in range(retries):
                try:
                    if format.lower() == "downloads":
                        self.get_post_downloads(post_url, download_path)
                    elif format.lower() == "content":
                        self.get_post_content(post_url, download_path)
                    elif format.lower() == "files":
                        # 注意：get_post_files 默认 format 是 config.format
                        self.get_post_files(post_url, download_path)
                    return None
                except Exception as e:
                    if attempt < retries - 1:
                        # 随着重试次数增加等待时间
                        wait_time = 2 * (attempt + 1)
                        logger.warning(f"处理失败，{wait_time}秒后重试 ({attempt + 1}/{retries}): {post_url} - {e}")
                        time.sleep(wait_time)
                    else:
                        # 在多线程中，使用 logging 记录错误
                        logger.error(f"Failed after {retries} attempts: {post_url} - {e}")
                        return f"Failed: {post_url} - {e}"
            return None

        # 使用 ThreadPoolExecutor 进行并发处理
        # 这里的 max_workers 可以根据需要调整，建议不要太大以免对服务器造成过大压力
        max_workers = self.config.max_workers
        
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {}
        
        try:
            # 提交所有任务
            futures = {executor.submit(process_post, url): url for url in url_lines}
            
            # 使用 tqdm 显示进度条
            with tqdm(total=total_posts, desc="Processing Posts", unit="post") as pbar:
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        # 如果有错误返回，打印出来 (tqdm.write 可以避免打断进度条)
                        tqdm.write(result)
                    pbar.update(1)
                    
            executor.shutdown(wait=True)
            
        except KeyboardInterrupt:
            logger.warning("用户中断下载，正在停止任务并清理临时文件...")
            
            # 尝试取消所有未完成的任务
            for f in futures:
                f.cancel()
            
            # 强制关闭线程池，不再等待正在运行的任务
            executor.shutdown(wait=False)
            
            # 清理下载目录中的 .tmp 文件
            for p in download_path.glob("*.tmp"):
                try:
                    p.unlink()
                except Exception:
                    pass

    def process_url(self, url: str) -> dict[str, int]:
        """
        处理输入URL，自动识别作者并下载。
        返回统计信息: {"success": 0, "failed": 0, "skipped": 0}
        """
        stats = {"success": 0, "failed": 0, "skipped": 0}
        print(f"✨ 正在解析魔法链接: {url}")
        
        if "/user/" in url:
            # 获取作者信息
            author_name = self.get_author_name(url) or "Unknown"
            
            # 获取所有帖子
            url_lines = self.get_author_artline(url)
            total_posts = len(url_lines)
            
            print(f"🎨 发现了一位创作者 (作者: {author_name})，共 {total_posts} 个帖子，准备下载...")
            
            downloads_path = self._resolve_download_path(None) / clean_filename(author_name)
            
            def process_one(post_url):
                # 简单重试逻辑
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        # 默认下载附件
                        files = self.get_post_downloads(post_url, downloads_path)
                        return bool(files)
                    except urllib.error.HTTPError as e:
                        if e.code == 429:
                            wait_time = (attempt + 1) * 2
                            time.sleep(wait_time)
                            continue
                        return False
                    except Exception:
                        return False
                return False

            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                futures = {executor.submit(process_one, u): u for u in url_lines}
                with tqdm(total=total_posts, desc="Downloading", unit="post") as pbar:
                    for future in as_completed(futures):
                        try:
                            if future.result():
                                stats["success"] += 1
                            else:
                                stats["skipped"] += 1
                        except Exception:
                            stats["failed"] += 1
                        pbar.update(1)
                        
        return stats

class KemonoDownloader:
    def __init__(self):
        self.kemono = Kemono()
        self.config = self.kemono.config
        self.max_workers = self.config.max_workers
        self._last_error = ""
        self.existing_sources = set()
        self._load_existing_sources()

    def _set_last_error(self, msg: str):
        self._last_error = msg or ""

    def _clear_last_error(self):
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
            print(f"📚 已加载 {len(self.existing_sources)} 条现有记录")
        except Exception:
            print("⚠️ 加载现有记录失败")
            return

    def _is_source_in_manifest(self, work_url: str) -> bool:
        if not work_url:
            return False
        return work_url.strip() in self.existing_sources

    def import_download(self, file_path: Path, work_url: str, author: str = "", series: str = "") -> tuple[Path | None, str]:
        if not file_path.exists():
            return None, "下载文件不存在"
        if not work_url:
            return None, "来源 URL 为空"
        safe_name = clean_filename(file_path.name)
        if file_path.name != safe_name:
            new_path = file_path.with_name(safe_name)
            file_path.rename(new_path)
            file_path = new_path
        file_type = determine_file_type(str(file_path))
        if file_type == "unknown":
            ext = file_path.suffix.lower()
            ext_display = ext[1:] if ext else ""
            config_path = get_project_root() / "config.json"
            return None, f"无法识别文件类型(ext={ext_display or '无'}; config={config_path})"
        base = get_library_path() / file_type
        determine_storage_path(base, author, series)
        target_path = build_import_target(file_path, author, series)
        shutil.move(str(file_path), str(target_path))
        file_md5 = generate_file_md5(target_path)
        args = SimpleNamespace(
            author=author,
            series=series,
            tags="",
            source=work_url,
        )
        metadata = create_metadata(args, target_path, target_path, file_md5)
        supplement_csv(metadata)
        return target_path, "ok"

    def _get_author_name(self, url: str) -> str:
        name = self.kemono.get_author_name(url)
        if name:
            return name
        match = re.search(r'/user/([^/]+)', url)
        return match.group(1) if match else "Unknown_Author"

    def _download_post(self, post_url: str, download_path: Path) -> list[Path]:
        files = self.kemono.get_post_downloads(post_url, download_path, self.config.if_download_cover)
        if not files:
            alt = self.kemono.get_post_files(post_url, download_path, format=self.config.format)
            if isinstance(alt, list):
                files = alt
            elif alt:
                files = [Path(alt)]
        if not files:
            self._set_last_error("下载失败或无文件")
            return []
        return [Path(f) for f in files]

    def download_and_import(self, post_url: str) -> tuple[list[Path], str]:
        self._clear_last_error()
        if self._is_source_in_manifest(post_url):
            return [], "清单已存在来源"
        author = self._get_author_name(post_url)
        download_path = self.kemono._resolve_download_path(self.config.default_download_path) / clean_filename(author)
        download_path.mkdir(parents=True, exist_ok=True)
        files = self._download_post(post_url, download_path)
        if not files:
            return [], self._last_error or "下载失败"
        imported = []
        for f in files:
            res, reason = self.import_download(Path(f), post_url, author=author)
            if res:
                imported.append(res)
            else:
                self._set_last_error(reason)
        if imported:
            self.existing_sources.add(post_url.strip())
            return imported, "ok"
        return [], self._last_error or "导入失败"

    def process_url(self, url: str) -> dict[str, int]:
        stats = {"success": 0, "failed": 0, "skipped": 0}
        print(f"✨ 正在解析魔法链接: {url}")
        if "/post/" in url:
            print("🎯 发现了一个作品链接，准备开始搬运...")
            res, reason = self.download_and_import(url)
            if res:
                stats["success"] += 1
                print(f"🎉 搬运成功: {Path(res[0]).name}")
            elif reason == "清单已存在来源":
                stats["skipped"] += 1
                print(f"💨 已跳过: {url} | 原因: {reason}")
            else:
                stats["failed"] += 1
                print(f"💢 搬运失败: {url} | 原因: {reason}")
            print(f"\n🎉 搬运大功告成啦: 🎉 成功 {stats['success']} | 💨 跳过 {stats['skipped']} | 💢 失败 {stats['failed']}")
            return stats
        if "/user/" in url:
            author = self._get_author_name(url)
            print(f"🎨 发现了一位创作者 (作者: {author})，正在翻阅作品集...")
            download_path = self.kemono._resolve_download_path(self.config.default_download_path) / clean_filename(author)
            download_path.mkdir(parents=True, exist_ok=True)
            posts = self.kemono.get_author_artline(url)
            total = len(posts)
            if total == 0:
                print("🍃 呜呜，什么都没有找到呢...")
                return stats
            lock = threading.Lock()
            print(f"🌸 哇！一共找到了 {total} 个宝藏，准备开始搬运啦~")
            print(f"✨ 召唤 {self.max_workers} 只搬运小精灵 (Max Workers: {self.max_workers})")
            with tqdm(total=total, unit="个", desc="📦 搬运进度", ncols=80, colour='pink') as pbar:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = []
                    for p in posts:
                        futures.append(executor.submit(self._download_worker, p, author, download_path, stats, lock, pbar))
                    for f in futures:
                        f.result()
            print(f"\n🎉 搬运大功告成啦: 🎉 成功 {stats['success']} | 💨 跳过 {stats['skipped']} | 💢 失败 {stats['failed']}")
            return stats
        print(f"❓ 这是什么奇怪的链接呀: {url}")
        return stats

    def _download_worker(self, post_url: str, author: str, download_path: Path, stats: dict, lock: threading.Lock, pbar: tqdm) -> None:
        try:
            if self._is_source_in_manifest(post_url):
                with lock:
                    pbar.write(f"💨 已跳过: {post_url} | 原因: 清单已存在来源")
                    stats["skipped"] += 1
                return
            files = self._download_post(post_url, download_path)
            if not files:
                with lock:
                    pbar.write(f"💢 搬运失败: {post_url} | 原因: {self._last_error or '下载失败'}")
                    stats["failed"] += 1
                return
            ok = False
            for f in files:
                res, _ = self.import_download(Path(f), post_url, author=author)
                if res:
                    ok = True
            with lock:
                if ok:
                    pbar.write(f"🎉 搬运成功: {Path(files[0]).name}")
                    stats["success"] += 1
                    self.existing_sources.add(post_url.strip())
                else:
                    pbar.write(f"💢 搬运失败: {post_url} | 原因: {self._last_error or '导入失败'}")
                    stats["failed"] += 1
        finally:
            pbar.update(1)

if __name__ == "__main__":
    import sys
    
    print("========================================")
    print("   Kemono Downloader Core Module")
    print("========================================")
    print("这是一个核心类库文件。")
    print("推荐运行方式：")
    print("1. Web 界面: python web_ui.py")
    print("2. 测试脚本: python test.py")
    print("----------------------------------------")
    
    url = input("如果想直接测试下载，请输入 URL (直接回车退出): ").strip()
    
    if url:
        try:
            d = KemonoDownloader()
            if "/post/" in url or "/user/" in url:
                d.process_url(url)
            else:
                print("无法识别的链接格式，请确保包含 /user/ 或 /post/")
        except KeyboardInterrupt:
            print("\n用户取消")
        except Exception as e:
            print(f"\n运行出错: {e}")
    else:
        print("已退出。")
