import json
import re
import requests
import time
import random
import concurrent.futures
import threading
from pathlib import Path
from tqdm import tqdm
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import html
import sys
import shutil
from types import SimpleNamespace
import csv
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from toolboxs import (
    get_project_root,
    clean_filename,
    convert_images_to_book,
    description_to_text,
    build_import_target,
    determine_storage_path,
    determine_file_type,
    get_library_path,
    create_metadata,
    supplement_csv,
    generate_file_md5,
)

class PixivDownloader:
    """
    Pixiv 下载器类。
    利用 config.json 中的 cookie 模拟网页版请求，下载插画和漫画。
    """
    
    def __init__(self):
        """
        初始化下载器，加载配置和 Cookie。
        """
        self.session = requests.Session()
        
        # 配置重试策略
        retry_strategy = Retry(
            total=5,  # 最大重试次数
            backoff_factor=1,  # 重试间隔 (1s, 2s, 4s, 8s...)
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        # 增加连接池大小，避免多线程下的阻塞
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,
            pool_maxsize=20
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self._last_error = ""
        self.max_workers = 4
        self.request_timeout = 15
        self.rate_limit_rps = 2.0
        self.retry_429 = True
        self.retry_429_delay_seconds = 30
        self.retry_429_max_workers = 2
        self._rate_lock = threading.Lock()
        self._rate_next_ts = 0.0
        self.existing_sources = set()
        self._load_existing_sources()
        self._load_config()

    def _load_existing_sources(self):
        """
        加载现有的下载记录，避免重复下载。
        """
        try:
            root = get_project_root()
            csv_path = root / "library_manifest.csv"
            if csv_path.exists():
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        src = row.get("来源") or row.get("source")
                        if src:
                            self.existing_sources.add(src.strip())
            print(f"📚 已加载 {len(self.existing_sources)} 条现有记录")
        except Exception as e:
            print(f"⚠️ 加载现有记录失败: {e}")

    def _load_config(self):
        """
        从 config.json 加载 Pixiv 配置 (URL, Headers, Cookie)。
        """
        try:
            root = get_project_root()
            config_path = root / "config.json"
            
            if not config_path.exists():
                print("警告: 配置文件 config.json 不存在。")
                return

            config = json.loads(config_path.read_text(encoding="utf-8"))
            
            # 加载 Pixiv 基础配置
            pixiv_config = config.get("pixiv_config", {})
            self.base_url = pixiv_config.get("base_url", "https://www.pixiv.net")
            self.ajax_url = pixiv_config.get("ajax_url", "https://www.pixiv.net/ajax/illust")
            
            # 加载 Headers
            headers = pixiv_config.get("headers", {})
            self.session.headers.update(headers)

            # 加载 Cookie
            cookie_str = config.get("cookie", {}).get("pixiv", "")
            if cookie_str:
                self.session.headers.update({"Cookie": cookie_str})
            else:
                print("警告: config.json 中未找到 pixiv cookie。")

            download_config = config.get("download", {})
            max_workers = download_config.get("max_workers")
            timeout = download_config.get("timeout")
            rate_limit_rps = download_config.get("rate_limit_rps")
            retry_429 = download_config.get("retry_429")
            retry_429_delay_seconds = download_config.get("retry_429_delay_seconds")
            retry_429_max_workers = download_config.get("retry_429_max_workers")
            if isinstance(max_workers, int) and max_workers > 0:
                self.max_workers = max_workers
            if isinstance(timeout, (int, float)) and timeout > 0:
                self.request_timeout = int(timeout)
            if isinstance(rate_limit_rps, (int, float)) and rate_limit_rps >= 0:
                self.rate_limit_rps = float(rate_limit_rps)
            if isinstance(retry_429, bool):
                self.retry_429 = retry_429
            if isinstance(retry_429_delay_seconds, (int, float)) and retry_429_delay_seconds >= 0:
                self.retry_429_delay_seconds = int(retry_429_delay_seconds)
            if isinstance(retry_429_max_workers, int) and retry_429_max_workers > 0:
                self.retry_429_max_workers = retry_429_max_workers
            else:
                self.retry_429_max_workers = max(1, self.max_workers // 2)
            
        except Exception as e:
            print(f"加载配置失败: {e}")
            # 设置默认 URL 以防万一
            self.base_url = "https://www.pixiv.net"
            self.ajax_url = "https://www.pixiv.net/ajax/illust"

    def _set_last_error(self, message: str) -> None:
        self._last_error = message or ""

    def _clear_last_error(self) -> None:
        self._last_error = ""

    def _rate_limit(self) -> None:
        if self.rate_limit_rps <= 0:
            return
        interval = 1.0 / self.rate_limit_rps
        wait = 0.0
        now = time.monotonic()
        with self._rate_lock:
            if self._rate_next_ts <= now:
                self._rate_next_ts = now + interval
            else:
                wait = self._rate_next_ts - now
                self._rate_next_ts += interval
        if wait > 0:
            time.sleep(wait)

    def get_info(self, work_url: str) -> Optional[Dict[str, Any]]:
        try:
            if "/artworks/" in work_url:
                m = re.search(r"/artworks/(\d+)", work_url)
                if not m:
                    self._set_last_error("作品ID解析失败")
                    return None
                pid = m.group(1)
                j = self._get_json_with_backoff(f"{self.ajax_url}/{pid}")
                if not j:
                    return None
                if j.get("error"):
                    self._set_last_error(j.get("message") or "作品信息接口返回错误")
                    return None
                b = j.get("body", {})
                tags_list = b.get("tags", {}).get("tags") or []
                tags = [t.get("tag") for t in tags_list if isinstance(t, dict) and t.get("tag")]
                series = None
                s = b.get("seriesNavData") or b.get("series")
                if isinstance(s, dict):
                    series = s.get("title") or s.get("seriesTitle")
                return {
                    "type": "illust",
                    "id": pid,
                    "author": b.get("userName"),
                    "title": clean_filename(b.get("title") or ""),
                    "series": series,
                    "tags": tags,
                    "description": description_to_text(b.get("description") or "")
                }
            if "/novel/show.php" in work_url:
                p = urlparse(work_url)
                nid = parse_qs(p.query).get("id", [None])[0]
                if not nid:
                    self._set_last_error("小说ID解析失败")
                    return None
                j = self._get_json_with_backoff(f"{self.base_url}/ajax/novel/{nid}")
                if not j:
                    return None
                if j.get("error"):
                    self._set_last_error(j.get("message") or "小说信息接口返回错误")
                    return None
                body = j.get("body", {}) if isinstance(j, dict) else {}
                title = body.get("title")
                author = body.get("userName") or body.get("user_name")
                tags = []
                t = body.get("tags")
                if isinstance(t, list):
                    tags = [x for x in t if isinstance(x, str)]
                elif isinstance(t, dict):
                    tl = t.get("tags") or []
                    tags = [x.get("tag") for x in tl if isinstance(x, dict) and x.get("tag")]
                description = body.get("caption") or body.get("description") or ""
                series = None
                s = body.get("seriesNavData") or body.get("series")
                if isinstance(s, dict):
                    series = s.get("title") or s.get("seriesTitle")
                if not title or not author:
                    try:
                        self._rate_limit()
                        h = self.session.get(work_url, timeout=15)
                        h.raise_for_status()
                        text = h.text
                    except requests.exceptions.RequestException as e:
                        status = getattr(getattr(e, "response", None), "status_code", None)
                        if status:
                            self._set_last_error(f"HTTP {status}: {work_url}")
                        else:
                            self._set_last_error(f"请求失败: {e}")
                        return None
                    og_title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', text)
                    og_desc = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', text)
                    if og_title and not title:
                        title = html.unescape(og_title.group(1))
                        if " - pixiv" in title:
                            title = title.split(" - pixiv")[0]
                        if " | " in title:
                            title = title.split(" | ")[0]
                        if " - " in title:
                            title = title.split(" - ")[0]
                    if og_desc and not description:
                        description = html.unescape(og_desc.group(1))
                return {
                    "type": "novel",
                    "id": nid,
                    "author": author,
                    "title": clean_filename(title or ""),
                    "series": series,
                    "tags": tags,
                    "description": description_to_text(description)
                }
            return None
        except Exception:
            self._set_last_error(f"解析失败: {work_url}")
            return None

    def _get_json_with_backoff(self, url: str, params: Optional[dict] = None, timeout: Optional[int] = None, max_retries: int = 4) -> Optional[dict]:
        for attempt in range(max_retries):
            try:
                effective_timeout = self.request_timeout if timeout is None else timeout
                self._rate_limit()
                time.sleep(random.uniform(0.15, 0.45))
                r = self.session.get(url, params=params, timeout=effective_timeout)
                if r.status_code == 429:
                    self._set_last_error(f"HTTP 429 Too Many Requests: {url}")
                    retry_after = r.headers.get("Retry-After")
                    wait = None
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except Exception:
                            wait = None
                    if wait is None:
                        wait = 2 * (attempt + 1) + random.uniform(0, 1)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.RequestException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status:
                    self._set_last_error(f"HTTP {status}: {url}")
                else:
                    self._set_last_error(f"请求失败: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None
            except Exception:
                self._set_last_error(f"解析失败: {url}")
                return None
        return None

    def get_user_name(self, user_id: str) -> Optional[str]:
        try:
            url = f"{self.base_url}/ajax/user/{user_id}"
            params = {"full": "1", "lang": "zh"}
            data = self._get_json_with_backoff(url, params=params)
            if not data or data.get("error"):
                return None
            body = data.get("body", {})
            if isinstance(body, dict):
                name = body.get("name") or body.get("userName") or body.get("user_name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            return None
        except Exception:
            return None

    def _unique_path(self, base: Path, stem: str, ext: str) -> Path:
        p = base / f"{stem}{ext}"
        if not p.exists():
            return p
        i = 1
        while True:
            q = base / f"{stem} ({i}){ext}"
            if not q.exists():
                return q
            i += 1
    def get_illust_pages(self, pid: str) -> List[str]:
        url = f"{self.ajax_url}/{pid}/pages"
        try:
            d = self._get_json_with_backoff(url)
            if not d:
                return []
            if d.get("error"):
                self._set_last_error(d.get("message") or "作品分页接口返回错误")
                return []
            pages = d.get("body", [])
            res = []
            for p in pages:
                u = p.get("urls", {})
                o = u.get("original_medium") or u.get("original")
                if o:
                    res.append(o)
            return res
        except Exception:
            self._set_last_error(f"解析失败: {url}")
            return []

    def download(self, work_url: str, save_dir: Optional[Path] = None) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
        if save_dir is None:
            save_dir = get_project_root() / "downloads"
        save_dir.mkdir(parents=True, exist_ok=True)
        info = self.get_info(work_url)
        if not info:
            return None, None
        title = info.get("title") or "untitled"
        title = clean_filename(title)
        if "/novel/show.php" in work_url:
            p = urlparse(work_url)
            nid = parse_qs(p.query).get("id", [None])[0]
            if not nid:
                self._set_last_error("小说ID解析失败")
                return None, None
            data = self._get_json_with_backoff(f"{self.base_url}/ajax/novel/{nid}", timeout=20)
            if not data or data.get("error"):
                if data and data.get("error"):
                    self._set_last_error(data.get("message") or "小说正文接口返回错误")
                return None, None
            body = data.get("body", {}) if isinstance(data, dict) else {}
            text = body.get("text") or body.get("content") or body.get("novelText") or ""
            if not text:
                try:
                    self._rate_limit()
                    h = self.session.get(work_url, timeout=20)
                    h.raise_for_status()
                except requests.exceptions.RequestException as e:
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    if status:
                        self._set_last_error(f"HTTP {status}: {work_url}")
                    else:
                        self._set_last_error(f"请求失败: {e}")
                    return None, None
                text = description_to_text(info.get("description") or "")
            out = self._unique_path(save_dir, title, ".txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            return out, info
        if "/artworks/" in work_url:
            m = re.search(r"/artworks/(\d+)", work_url)
            if not m:
                self._set_last_error("作品ID解析失败")
                return None, None
            pid = m.group(1)
            urls = self.get_illust_pages(pid)
            if not urls:
                if not self._last_error:
                    self._set_last_error("获取作品图片列表失败")
                return None, None
            tmp_dir = save_dir / f"{title}__{pid}"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            for u in urls:
                fn = u.split("/")[-1]
                fp = tmp_dir / fn
                if fp.exists():
                    continue
                if not self._download_file(u, fp):
                    return None, None
            pdf_path = convert_images_to_book(tmp_dir, target_format="pdf", delete_original=True)
            final = self._unique_path(save_dir, title, ".pdf")
            pdf_path.rename(final)
            return final, info
        return None, None
    def _download_file(self, url: str, save_path: Path) -> bool:
        try:
            self._rate_limit()
            with self.session.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return True
        except requests.exceptions.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status:
                self._set_last_error(f"HTTP {status}: {url}")
            else:
                self._set_last_error(f"请求失败: {e}")
            return False
        except Exception:
            self._set_last_error(f"下载失败: {url}")
            return False
    def import_download(self, file_path: Path, work_url: Optional[str] = None, info: Optional[Dict[str, Any]] = None) -> tuple[Optional[Path], str]:
        if not file_path.exists():
            return None, "下载文件不存在"
        if not work_url:
            return None, "来源 URL 为空"
        if info is None:
            info = self.get_info(work_url)
        if not info:
            return None, self._last_error or "获取作品信息失败"
        title = clean_filename(info.get("title") or "")
        author = info.get("author") or ""
        series = info.get("series") or ""
        suffix = file_path.suffix
        expected_name = f"{title}{suffix}" if title else file_path.name
        if file_path.name != expected_name:
            new_path = file_path.with_name(expected_name)
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
        tags = info.get("tags") or []
        tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
        args = SimpleNamespace(
            author=author,
            series=series,
            tags=tags_str,
            source=work_url,
        )
        metadata = create_metadata(args, target_path, target_path, file_md5)
        supplement_csv(metadata)
        return target_path, "ok"
    def download_and_import(self, work_url: str) -> tuple[Optional[Path], str]:
        self._clear_last_error()
        if self._is_source_in_manifest(work_url):
            return None, "清单已存在来源"
        
        dl, info = self.download(work_url)
        if not dl:
            return None, self._last_error or "下载失败"
        
        res, reason = self.import_download(dl, work_url, info)
        if res:
            self.existing_sources.add(work_url.strip())
            return res, "ok"
        return None, reason

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

    def _is_source_in_manifest(self, work_url: str) -> bool:
        if not work_url:
            return False
        work_url = work_url.strip()
        csv_path = self._get_manifest_path()
        if not csv_path.exists():
            return False
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    src = row.get("来源") or row.get("source")
                    if src and src.strip() == work_url:
                        return True
        except Exception:
            return False
        return False

    def get_user_works(self, user_id: str) -> List[str]:
        """
        获取用户所有作品（插画、漫画、小说）的URL列表。
        """
        url = f"{self.base_url}/ajax/user/{user_id}/profile/all"
        try:
            data = self._get_json_with_backoff(url)
            if not data or data.get("error"):
                return []
            
            body = data.get("body", {})
            works = []
            
            # Illusts
            illusts = body.get("illusts", {})
            if isinstance(illusts, dict):
                works.extend([f"https://www.pixiv.net/artworks/{pid}" for pid in illusts.keys()])
            
            # Manga
            manga = body.get("manga", {})
            if isinstance(manga, dict):
                works.extend([f"https://www.pixiv.net/artworks/{pid}" for pid in manga.keys()])
                
            # Novels
            novels = body.get("novels", {})
            if isinstance(novels, dict):
                works.extend([f"https://www.pixiv.net/novel/show.php?id={nid}" for nid in novels.keys()])
                
            return works
        except Exception as e:
            print(f"获取用户作品失败: {e}")
            return []

    def get_series_works(self, series_id: str, is_novel: bool = False) -> List[str]:
        """
        获取系列作品URL列表。
        """
        works = []
        if is_novel:
            url = f"{self.base_url}/ajax/novel/series/{series_id}/content_titles"
            try:
                data = self._get_json_with_backoff(url, timeout=15)
                if data and not data.get("error"):
                    body = data.get("body", [])
                    for item in body:
                        if "id" in item:
                            works.append(f"https://www.pixiv.net/novel/show.php?id={item['id']}")
            except Exception as e:
                print(f"获取小说系列失败: {e}")
        else:
            page = 1
            while True:
                url = f"{self.base_url}/ajax/series/{series_id}"
                params = {"p": page, "limit": 30, "lang": "zh"}
                try:
                    data = self._get_json_with_backoff(url, params=params)
                    if not data or data.get("error"):
                        break
                    body = data.get("body", {})
                    page_works = body.get("work", [])
                    if not page_works:
                        break
                    for w in page_works:
                        if "id" in w:
                            works.append(f"https://www.pixiv.net/artworks/{w['id']}")
                    if len(page_works) < 30:
                        break
                    page += 1
                except Exception as e:
                    print(f"获取插画系列失败: {e}")
                    break
        return works

    def process_url(self, url: str) -> Dict[str, int]:
        """
        处理输入URL，自动识别作品、用户或系列，并下载导入。
        返回统计信息: {"success": 0, "failed": 0, "skipped": 0}
        """
        stats = {"success": 0, "failed": 0, "skipped": 0}
        works = []
        
        print(f"✨ 正在解析魔法链接: {url}")
        
        # User Profile
        if "/users/" in url:
            m = re.search(r"/users/(\d+)", url)
            if m:
                uid = m.group(1)
                author_name = self.get_user_name(uid) or uid
                print(f"🎨 发现了一位画师大大 (作者: {author_name})，正在翻阅作品集...")
                works = self.get_user_works(uid)
        
        # Novel Series
        elif "/novel/series/" in url:
            m = re.search(r"/novel/series/(\d+)", url)
            if m:
                sid = m.group(1)
                print(f"📚 发现了一套精彩的小说 (ID: {sid})，正在整理章节...")
                works = self.get_series_works(sid, is_novel=True)
                
        # Illust/Manga Series (user/x/series/y)
        elif "/series/" in url:
            m = re.search(r"/series/(\d+)", url)
            if m:
                sid = m.group(1)
                print(f"🎨 发现了一套精美的插画 (ID: {sid})，正在整理画集...")
                works = self.get_series_works(sid, is_novel=False)
                
        # Single Work
        elif "/artworks/" in url or "/novel/show.php" in url:
            works = [url]
            
        else:
            print(f"❓ 这是什么奇怪的链接呀: {url}")
            return stats

        total = len(works)
        if total == 0:
            print("🍃 呜呜，什么都没有找到呢...")
            return stats

        print(f"🌸 哇！一共找到了 {total} 个宝藏，准备开始搬运啦~")
        
        # 使用多线程下载
        # 适当增加线程数，但保持在合理范围，配合重试策略
        max_workers = self.max_workers
        print(f"� 召唤 {max_workers} 只搬运小精灵 (Max Workers: {max_workers})")
        
        lock = threading.Lock()
        
        with tqdm(total=total, unit="个", desc="📦 搬运进度", ncols=80, colour='pink') as pbar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(self._download_worker, url, stats, lock, pbar, True)
                    for url in works
                ]
                concurrent.futures.wait(futures)
                retry_urls = []
                if self.retry_429:
                    for f in futures:
                        try:
                            work_url, ok, reason = f.result()
                        except Exception as e:
                            work_url, ok, reason = "", False, str(e)
                        if work_url and not ok and reason and "429" in reason:
                            retry_urls.append(work_url)
                if retry_urls:
                    time.sleep(self.retry_429_delay_seconds)
                    with tqdm(total=len(retry_urls), unit="个", desc="♻️ 限流重试", ncols=80, colour='pink') as rpbar:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=self.retry_429_max_workers) as rex:
                            rfutures = [
                                rex.submit(self._download_worker, url, stats, lock, rpbar, False)
                                for url in retry_urls
                            ]
                            concurrent.futures.wait(rfutures)
                            for f in rfutures:
                                try:
                                    _, ok, _ = f.result()
                                except Exception:
                                    ok = False
                                if ok:
                                    with lock:
                                        stats["success"] += 1
                                        if stats["skipped"] > 0:
                                            stats["skipped"] -= 1
        
        print(f"\n� 搬运大功告成啦: 🎉 成功 {stats['success']} | 💨 跳过 {stats['skipped']} | 💢 失败 {stats['failed']}")
        return stats
        
    def _download_worker(self, work_url: str, stats: Dict[str, int], lock: threading.Lock, pbar: tqdm, count_stats: bool):
        """
        线程工作函数，包含额外的重试逻辑
        """
        # Random delay to avoid burst requests
        time.sleep(random.uniform(0.5, 1.5))
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                res, reason = self.download_and_import(work_url)
                
                with lock:
                    if res:
                        pbar.write(f"🎉 搬运成功: {res.name}")
                        if count_stats:
                            stats["success"] += 1
                    else:
                        pbar.write(f"💨 已跳过: {work_url} | 原因: {reason}")
                        if count_stats:
                            stats["skipped"] += 1
                    pbar.update(1)
                return work_url, bool(res), reason
                
            except requests.exceptions.RequestException as e:
                # 网络错误进行重试
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))  # 指数退避
                    continue
                else:
                    with lock:
                        pbar.write(f"🌐 网络不太好呢 {work_url}: {e}")
                        if count_stats:
                            stats["failed"] += 1
                        pbar.update(1)
                    return work_url, False, str(e)
            except Exception as e:
                with lock:
                    pbar.write(f"💢 哎呀出错惹 {work_url}: {e}")
                    if count_stats:
                        stats["failed"] += 1
                    pbar.update(1)
                return work_url, False, str(e)

    

if __name__ == "__main__":
    d = PixivDownloader()
    # 示例用法:
    # 1. 下载单个作品
    # u1 = "https://www.pixiv.net/artworks/141134416"
    # d.process_url(u1)
    
    # 2. 下载用户所有作品 (示例用户)
    u_user = "https://www.pixiv.net/users/66902618"
    d.process_url(u_user)
    
    # 3. 下载系列作品
    # u_series = "https://www.pixiv.net/novel/series/123456" 
    # d.process_url(u_series)
