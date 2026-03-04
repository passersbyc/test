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
from typing import Optional, Dict, Any, List, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from tqdm import tqdm

project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from src.cli.downplugin.base import setup_logging, BaseDownloader
from toolboxs import (
    clean_filename, 
    description_to_text, 
    convert_images_to_book, 
    get_project_root, 
    logger
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
# setup_logging("application.log")
# logger = logging.getLogger(__name__)

class KemonoDownloader(BaseDownloader):
    """
    Kemono 站点下载器，包含爬虫与下载管理功能。
    """
    def __init__(self):
        super().__init__()
        self.config = load_kemono_config()
        self.base_url = self.config.base_url
        self.headers = self.config.headers
        self.max_workers = self.config.max_workers
        self._load_existing_sources()

    # ==================== Kemono Logic Integration ====================

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
    
    def get_user_works(self, author_id_or_url, service=None):
        match = re.search(r'/([^/]+)/user/([^/]+)', author_id_or_url)
        if match:
            service = match.group(1)
            author_id = match.group(2)
        else:
            author_id = author_id_or_url
            if not service:
                service = "patreon"

        seen = set()
        result = []
        offset = 0
        
        while True:
            api = f"{self.base_url}api/v1/{service}/user/{author_id}/posts?o={offset}"
            headers = dict(self.headers)
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
                if e.code == 400:
                    break
                if e.code == 429:
                    logger.warning(f"HTTP 429 Too Many Requests. Waiting 30s before retry... (Offset {offset})")
                    time.sleep(30)
                    continue
                break
            except Exception as e:
                logger.warning(f"抓取中断或结束 (Offset {offset}): {e}")
                break
                
            try:
                items = json.loads(data)
            except Exception:
                break
                
            if not items:
                break
                
            new_count = 0
            for it in items:
                pid = str(it.get("id"))
                if not pid:
                    continue
                link = f"{self.base_url}{service}/user/{author_id}/post/{pid}"
                if link not in seen:
                    seen.add(link)
                    result.append(link)
                    new_count += 1
            
            if new_count == 0:
                break
            offset += 50
        return result

    def get_author_name(self, author_id_or_url, service=None):
        match = re.search(r'/([^/]+)/user/([^/]+)', author_id_or_url)
        if match:
            service = match.group(1)
            author_id = match.group(2)
        else:
            author_id = author_id_or_url
            if not service:
                service = "patreon"

        api_url = f"{self.base_url}api/v1/{service}/user/{author_id}/profile"
        headers = dict(self.headers)
        headers["Accept"] = "text/css"

        while True:
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
                    return name
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    logger.warning(f"HTTP 429 Too Many Requests. Waiting 30s before retry... (Profile)")
                    time.sleep(30)
                    continue
                return None
            except Exception as e:
                logger.error(f"获取作者名称失败: {e}")
                return None

    def get_author_info(self, url: str) -> Optional[tuple[str, int]]:
        if "/user/" not in url or "/post/" in url:
            return None
        name = self.get_author_name(url)
        if not name:
            return None
        posts = self.get_user_works(url)
        return name, len(posts)

    def get_post_downloads(self, post_url, downloads_path=None, if_download_cover=None):
        match = re.search(r'/([^/]+)/user/([^/]+)/post/([^/]+)', post_url)
        if not match:
            logger.error(f"无法从 URL 解析帖子信息: {post_url}")
            return

        service, user_id, post_id = match.groups()
        api_url = f"{self.base_url}api/v1/{service}/user/{user_id}/post/{post_id}"
        
        headers = dict(self.headers)
        headers["Accept"] = "text/css"
        
        post_data = None
        while True:
            try:
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    raw_data = resp.read()
                    if resp.info().get('Content-Encoding') == 'gzip' or raw_data.startswith(b'\x1f\x8b'):
                        data = gzip.decompress(raw_data).decode("utf-8", "ignore")
                    else:
                        data = raw_data.decode("utf-8", "ignore")
                    post_data = json.loads(data)
                    break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    logger.warning(f"HTTP 429 Too Many Requests. Waiting 30s before retry... (Post {post_id})")
                    time.sleep(30)
                    continue
                return
            except Exception:
                return

        if if_download_cover is None:
            if_download_cover = self.config.if_download_cover

        downloads_path = self._resolve_download_path(downloads_path)
        if not downloads_path.exists():
            downloads_path.mkdir(parents=True, exist_ok=True)

        files_to_download = []
        path_to_server = {}
        if 'attachments' in post_data:
             for att in post_data['attachments']:
                 if 'path' in att and 'server' in att:
                     path_to_server[att['path']] = att['server']

        post_info = post_data.get('post', {})
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

        downloaded_files = []
        for file_info in files_to_download:
            file_path = file_info.get('path')
            file_name = file_info.get('name')
            if not file_path:
                continue
            server = path_to_server.get(file_path)
            if not server:
                server = self.config.default_image_server
            if not file_name:
                file_name = f"{Path(file_path).name}"
            safe_name = clean_filename(file_name)
            save_path = downloads_path / safe_name
            temp_path = downloads_path / f"{safe_name}.tmp"
            
            if save_path.exists():
                downloaded_files.append(save_path)
                continue
            
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
                        downloaded_files.append(save_path)
                        success = True
                        break
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
        match = re.search(r'/([^/]+)/user/([^/]+)/post/([^/]+)', post_url)
        if not match:
            return None

        service, user_id, post_id = match.groups()
        api_url = f"{self.base_url}api/v1/{service}/user/{user_id}/post/{post_id}"
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
        except Exception:
            return None

        post_info = post_data.get('post', {})
        title = post_info.get('title', 'Untitled')
        content = post_info.get('content', '')
        if not content:
            return
        content = description_to_text(content)

        downloads_path = self._resolve_download_path(downloads_path)
        if not downloads_path.exists():
            downloads_path.mkdir(parents=True, exist_ok=True)
            
        safe_title = clean_filename(title)
        if len(safe_title) > 200:
            safe_title = safe_title[:200]
        file_path = downloads_path / f"{safe_title}.txt"
        temp_path = downloads_path / f"{safe_title}.txt.tmp"
        
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(content)
            temp_path.rename(file_path)
            return file_path
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
        return None

    def get_post_files(self, post_url, downloads_path=None, format=None):
        match = re.search(r'/([^/]+)/user/([^/]+)/post/([^/]+)', post_url)
        if not match:
            return None
        service, user_id, post_id = match.groups()
        api_url = f"{self.base_url}api/v1/{service}/user/{user_id}/post/{post_id}"
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
        except Exception:
            return None

        post_info = post_data.get('post', {})
        title = post_info.get('title', 'Untitled')
        image_urls = []
        if 'attachments' in post_info and post_info['attachments']:
            for att in post_info['attachments']:
                if 'path' in att:
                    ext = Path(att['path']).suffix.lower()
                    if ext in self.config.image_extensions:
                        if att['path'] not in image_urls:
                            image_urls.append(att['path'])
        if not image_urls:
            return None
            
        safe_title = clean_filename(title)
        if len(safe_title) > 200:
            safe_title = safe_title[:200]
        downloads_path = self._resolve_download_path(downloads_path)
        target_dir = downloads_path / safe_title
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
            
        downloaded_files = []
        servers = self._image_servers(self.config.default_image_server)
        for i, img_path in enumerate(image_urls):
            ext = Path(img_path).suffix
            if not ext:
                ext = ".jpg"
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
                        req = urllib.request.Request(dl_url, headers=self.headers)
                        with urllib.request.urlopen(req, timeout=self.config.timeout) as dl_resp, open(temp_path, 'wb') as f:
                            f.write(dl_resp.read())
                        temp_path.rename(save_path)
                        downloaded_files.append(save_path)
                        success = True
                        break
                    except Exception:
                        if attempt < attempts - 1:
                            time.sleep(1 + attempt)
                if success:
                    break
            if not success and temp_path.exists():
                temp_path.unlink()

        if not downloaded_files:
            if not any(target_dir.iterdir()):
                target_dir.rmdir()
            return None

        if format is None:
            format = self.config.format
        if format.lower() == "image":
            return downloaded_files
        elif format.lower() in {"pdf", "cbz"}:
            try:
                output_file = convert_images_to_book(target_dir, target_format=format.lower(), delete_original=True)
                return output_file
            except Exception:
                pass
        return None

    # ==================== Downloader High-Level Logic ====================

    def _get_author_name_cached(self, url: str) -> str:
        # Wrapper to use in downloader context
        name = self.get_author_name(url)
        if name:
            return name
        match = re.search(r'/user/([^/]+)', url)
        return match.group(1) if match else "Unknown_Author"

    def _write_to_blacklist(self, url: str, reason: str):
        blacklist_path = get_project_root() / "blacklist.csv"
        file_exists = blacklist_path.exists()
        
        try:
            with open(blacklist_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["URL", "Reason", "Timestamp"])
                writer.writerow([url, reason, time.strftime("%Y-%m-%d %H:%M:%S")])
        except Exception as e:
            logger.error(f"写入黑名单失败: {e}")

    def _download_post(self, post_url: str, download_path: Path) -> list[Path]:
        # 1. 尝试下载附件
        files = self.get_post_downloads(post_url, download_path, self.config.if_download_cover)
        
        is_api_error = (files is None)

        # 2. 如果附件未找到或下载失败，尝试作为备选方案下载正文图片/文件
        if not files:
            alt = self.get_post_files(post_url, download_path, format=self.config.format)
            if isinstance(alt, list):
                files = alt
            elif alt:
                files = [Path(alt)]
            
            # 如果两次尝试都返回 None，说明很有可能是网络请求或解析阶段就挂了
            if is_api_error and alt is None:
                 self._set_last_error(f"获取帖子详情失败 (API请求错误或解析失败): {post_url}")
                 # API 错误暂时不加入黑名单，因为可能是临时的
                 return []

        if not files:
            # files 是空列表，说明 API 调用成功了 (get_post_downloads 返回了 [])，但确实没找到东西
            msg = f"跳过: 帖子无附件且无正文图片 (或被过滤): {post_url}"
            self._set_last_error(msg)
            # 写入黑名单
            self._write_to_blacklist(post_url, "No attachments or images found")
            return []
        return [Path(f) for f in files]

    def download_and_import(self, post_url: str) -> tuple[list[Path], str]:
        self._clear_last_error()
        if self._is_source_in_manifest(post_url):
            return [], "清单已存在来源"
        author = self._get_author_name_cached(post_url)
        download_path = self._resolve_download_path(self.config.default_download_path) / clean_filename(author)
        download_path.mkdir(parents=True, exist_ok=True)
        files = self._download_post(post_url, download_path)
        if not files:
            return [], self._last_error or "下载失败"
        imported = []
        
        metadata = {
            "author": author,
            "source": post_url,
            "tags": []
        }
        
        for f in files:
            res, reason = self.import_download(Path(f), post_url, metadata)
            if res:
                imported.append(res)
            else:
                self._set_last_error(reason)
        if imported:
            self.existing_sources.add(post_url.strip())
            return imported, "ok"
        return [], self._last_error or "导入失败"

    def process_url(self, url: str | List[str]) -> dict[str, int]:
        stats = {"success": 0, "failed": 0, "skipped": 0}
        works = []
        author = "Unknown"
        download_path = self._resolve_download_path(self.config.default_download_path)

        if isinstance(url, list):
            # 列表模式：直接视为作品链接
            works = [u.strip() for u in url if u.strip()]
            # print(f"✨ 收到 {len(works)} 个作品链接，准备批量搬运...") # 移除重复日志
        else:
            # 单链接模式：执行解析逻辑
            u = url.strip()
            if not u:
                return stats
                
            print(f"✨ 正在解析魔法链接: {u}")
            
            if "/post/" in u:
                print("🎯 发现了一个作品链接，准备开始搬运...")
                works.append(u)
                
            elif "/user/" in u:
                author_name = self._get_author_name_cached(u)
                print(f"🎨 发现了一位创作者 (作者: {author_name})，正在翻阅作品集...")
                # 用户主页的所有作品
                works.extend(self.get_user_works(u))
                
            else:
                print(f"❓ 这是什么奇怪的链接呀: {u}")
                return stats

        # 去重
        works = list(dict.fromkeys(works))
        total = len(works)
        if total == 0:
            print("🍃 呜呜，什么都没有找到呢...")
            return stats

        print(f"🌸 哇！一共找到了 {total} 个宝藏，准备开始搬运啦~")
        print(f"🚀 召唤 {self.max_workers} 只搬运小精灵 (Max Workers: {self.max_workers})")
        
        lock = threading.Lock()
        
        with tqdm(total=total, unit="个", desc="📦 搬运进度", ncols=80, colour='MAGENTA') as pbar:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self._download_worker, p, "Unknown", download_path, stats, lock, pbar): p
                    for p in works
                }
                
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as e:
                        logger.error(f"任务执行异常: {e}")
                        
        print(f"\n🎉 搬运大功告成啦: 🎉 成功 {stats['success']} | 💨 跳过 {stats['skipped']} | 💢 失败 {stats['failed']}")
        return stats

    def _download_worker(self, post_url: str, author: str, download_path: Path, stats: dict, lock: threading.Lock, pbar: tqdm) -> None:
        try:
            if self._is_source_in_manifest(post_url):
                with lock:
                    stats["skipped"] += 1
                return

            max_retries = 3
            files = []
            
            for attempt in range(max_retries):
                try:
                    files = self._download_post(post_url, download_path)
                    # 只要 _download_post 正常返回（无论是有文件还是空列表），都说明逻辑已执行完毕
                    # 不需要重试，直接跳出循环
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(2 * (attempt + 1))
                    else:
                        logger.error(f"下载失败 {post_url}: {e}")

            if files:
                metadata = {"author": author, "source": post_url, "tags": []}
                any_imported = False
                for f in files:
                    res, reason = self.import_download(Path(f), post_url, metadata)
                    if res:
                        any_imported = True
                    else:
                        logger.warning(f"导入失败 {f.name}: {reason}")
                
                with lock:
                    if any_imported:
                        stats["success"] += 1
                        self.existing_sources.add(post_url.strip())
                    else:
                        stats["failed"] += 1
            else:
                # files 为空，说明没有下载到文件 (被过滤或无内容)
                # 这种情况视为 "skipped" 而不是 "failed"
                with lock:
                    stats["skipped"] += 1
                    
        except Exception as e:
            logger.error(f"Worker Error: {e}")
            with lock:
                stats["failed"] += 1
        finally:
            pbar.update(1)

if __name__ == "__main__":
    print("========================================")
    print("   Kemono Downloader Core Module")
    print("========================================")
    
    url ="https://kemono.cr/fanbox/user/73111346"
    
    if url:
        try:
            d = KemonoDownloader()
            d.process_url(url)
        except KeyboardInterrupt:
            print("\n用户取消")
        except Exception as e:
            print(f"\n运行出错: {e}")   
