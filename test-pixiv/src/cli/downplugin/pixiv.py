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
import zipfile
import mimetypes
import logging
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

setup_logging()
logger = logging.getLogger(__name__)

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
        self.image_rate_limit_rps = 6.0
        self._image_rate_lock = threading.Lock()
        self._image_rate_next_ts = 0.0
        self.existing_sources = set()
        self._load_existing_sources()
        self._load_config()
        self.download_file_path = "downloads"

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
            logger.error(f"加载现有记录失败: {e}")

    def _load_config(self):
        """
        从 config.json 加载 Pixiv 配置 (URL, Headers, Cookie)。
        """
        try:
            root = get_project_root()
            config_path = root / "config.json"
            
            if not config_path.exists():
                print("警告: 配置文件 config.json 不存在。")
                logger.error("配置文件 config.json 不存在。")
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
            download_file_path = config.get("download_file_path")
            max_workers = download_config.get("max_workers")
            timeout = download_config.get("timeout")
            rate_limit_rps = download_config.get("rate_limit_rps")
            image_rate_limit_rps = download_config.get("image_rate_limit_rps")
            retry_429 = download_config.get("retry_429")
            retry_429_delay_seconds = download_config.get("retry_429_delay_seconds")
            retry_429_max_workers = download_config.get("retry_429_max_workers")
            if isinstance(max_workers, int) and max_workers > 0:
                self.max_workers = max_workers
            if isinstance(timeout, (int, float)) and timeout > 0:
                self.request_timeout = int(timeout)
            if isinstance(rate_limit_rps, (int, float)) and rate_limit_rps >= 0:
                self.rate_limit_rps = float(rate_limit_rps)
            if isinstance(image_rate_limit_rps, (int, float)) and image_rate_limit_rps >= 0:
                self.image_rate_limit_rps = float(image_rate_limit_rps)
            if isinstance(retry_429, bool):
                self.retry_429 = retry_429
            if isinstance(retry_429_delay_seconds, (int, float)) and retry_429_delay_seconds >= 0:
                self.retry_429_delay_seconds = int(retry_429_delay_seconds)
            if isinstance(retry_429_max_workers, int) and retry_429_max_workers > 0:
                self.retry_429_max_workers = retry_429_max_workers
            else:
                self.retry_429_max_workers = max(1, self.max_workers // 2)
            if isinstance(download_file_path, str) and download_file_path.strip():
                self.download_file_path = download_file_path.strip()
            
        except Exception as e:
            print(f"加载配置失败: {e}")
            logger.error(f"加载配置失败: {e}")
            # 设置默认 URL 以防万一
            self.base_url = "https://www.pixiv.net"
            self.ajax_url = "https://www.pixiv.net/ajax/illust"

    def _set_last_error(self, message: str) -> None:
        self._last_error = message or ""
        if message:
            logger.error(message)

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

    def _rate_limit_image(self) -> None:
        if self.image_rate_limit_rps <= 0:
            return
        interval = 1.0 / self.image_rate_limit_rps
        wait = 0.0
        now = time.monotonic()
        with self._image_rate_lock:
            if self._image_rate_next_ts <= now:
                self._image_rate_next_ts = now + interval
            else:
                wait = self._image_rate_next_ts - now
                self._image_rate_next_ts += interval
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
    def _guess_image_meta(self, url: str, content_type: Optional[str]) -> tuple[str, str]:
        mime = None
        if content_type:
            mime = content_type.split(";")[0].strip()
        ext = mimetypes.guess_extension(mime) if mime else None
        if not ext and url:
            ext = Path(urlparse(url).path).suffix
        if not ext:
            ext = ".jpg"
        if not mime:
            mime = mimetypes.types_map.get(ext.lower(), "image/jpeg")
        return ext, mime

    def _download_binary(self, url: str, timeout: int = 30, use_image_limit: bool = False) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
        try:
            if use_image_limit:
                self._rate_limit_image()
            else:
                self._rate_limit()
            r = self.session.get(url, timeout=timeout)
            if not r.ok or not r.content:
                return None, None, None
            ext, mime = self._guess_image_meta(url, r.headers.get("Content-Type"))
            return r.content, ext, mime
        except requests.exceptions.RequestException:
            return None, None, None

    def _extract_tags(self, body: dict) -> list[str]:
        tags = body.get("tags")
        res = []
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t.strip():
                    res.append(t.strip())
                elif isinstance(t, dict) and t.get("tag"):
                    res.append(str(t.get("tag")).strip())
        elif isinstance(tags, dict):
            tl = tags.get("tags") or []
            for t in tl:
                if isinstance(t, dict) and t.get("tag"):
                    res.append(str(t.get("tag")).strip())
                elif isinstance(t, str) and t.strip():
                    res.append(t.strip())
        return [t for t in res if t]

    def _collect_image_url(self, value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            url = value.get("url") or value.get("original") or value.get("originalUrl")
            if url:
                return str(url)
            urls = value.get("urls") or value.get("imageUrls") or value.get("image_urls")
            if isinstance(urls, dict):
                for key in ["original", "originalImageUrl", "raw", "1200x1200", "large", "regular", "medium", "small"]:
                    u = urls.get(key)
                    if u:
                        return str(u)
        return None

    def _extract_novel_images(self, body: dict) -> dict[str, str]:
        result = {}
        candidates = []
        for key in ["textEmbeddedImages", "images", "imageUrls", "image_urls", "illusts", "illustImages", "illustsMap", "uploadedImages", "uploadedImage", "uploadedimage"]:
            v = body.get(key)
            if v:
                candidates.append(v)
        for obj in candidates:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, dict):
                        vid = v.get("novelImageId") or v.get("id") or v.get("imageId") or v.get("image_id") or v.get("illustId") or v.get("illust_id")
                        url = self._collect_image_url(v)
                        if url:
                            result[str(vid or k)] = url
                    else:
                        url = self._collect_image_url(v)
                        if url:
                            result[str(k)] = url
            elif isinstance(obj, list):
                for idx, v in enumerate(obj):
                    if isinstance(v, dict):
                        vid = v.get("id") or v.get("imageId") or v.get("image_id") or v.get("illustId") or v.get("illust_id")
                        url = self._collect_image_url(v)
                        if url:
                            result[str(vid or idx)] = url
                    else:
                        url = self._collect_image_url(v)
                        if url:
                            result[str(idx)] = url
        return result

    def _resolve_novel_image_url(self, image_id: str, images: dict[str, str]) -> Optional[str]:
        if image_id in images:
            return images[image_id]
        return None

    def _build_inline_images(self, text: str, body: dict, prefix: str = "") -> tuple[str, list[dict]]:
        images = self._extract_novel_images(body)
        pattern = re.compile(r"\[(?:pixivimage|uploadedimage):(\d+)\]")
        text = text or ""
        matches = list(pattern.finditer(text))
        if not matches:
            return text, []
        image_ids = [m.group(1) for m in matches]
        url_map = {}
        for image_id in set(image_ids):
            url_map[image_id] = self._resolve_novel_image_url(image_id, images)
        cache = {}
        tasks = {}
        max_workers = max(1, min(self.max_workers, 6))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for image_id, url in url_map.items():
                if not url:
                    continue
                tasks[image_id] = executor.submit(self._download_binary, url, 30, True)
            for image_id, future in tasks.items():
                data, ext, mime = future.result()
                if data and ext and mime:
                    cache[image_id] = (data, ext, mime)
        parts = []
        last = 0
        inline_images = []
        for idx, m in enumerate(matches, start=1):
            parts.append(text[last:m.start()])
            image_id = m.group(1)
            cached = cache.get(image_id)
            if cached:
                data, ext, mime = cached
                href = f"images/inline_{prefix}{idx}{ext}"
                placeholder = f"__PIXIV_IMAGE_{prefix}{idx}__"
                inline_images.append({
                    "placeholder": placeholder,
                    "href": href,
                    "bytes": data,
                    "mime": mime
                })
                parts.append(placeholder)
            last = m.end()
        parts.append(text[last:])
        replaced = "".join(parts)
        return replaced, inline_images

    def _write_epub(self, text: str, metadata: dict, identifier: str, output_path: Path, cover_bytes: Optional[bytes] = None, cover_ext: Optional[str] = None, cover_mime: Optional[str] = None, inline_images: Optional[list[dict]] = None, chapters: Optional[list[dict]] = None) -> bool:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            title = metadata.get("title") or "untitled"
            author = metadata.get("author") or ""
            description = metadata.get("description") or ""
            tags = metadata.get("tags") or []
            pub_date = metadata.get("date") or ""
            safe_title = html.escape(title)
            safe_author = html.escape(author)
            safe_id = html.escape(identifier or title or "pixiv-novel")
            safe_desc = html.escape(description)
            inline_images = inline_images or []
            placeholder_map = {i["placeholder"]: i for i in inline_images if i.get("placeholder")}
            placeholder_pattern = None
            if placeholder_map:
                placeholder_pattern = re.compile("(" + "|".join(re.escape(k) for k in placeholder_map.keys()) + ")")
            body_parts = []
            def render_text(txt: str):
                raw = (txt or "").replace("\r\n", "\n")
                blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
                if not blocks:
                    blocks = [b.strip() for b in raw.splitlines() if b.strip()]
                for b in blocks:
                    if placeholder_pattern and placeholder_pattern.search(b):
                        parts = placeholder_pattern.split(b)
                        for part in parts:
                            if part in placeholder_map:
                                href = placeholder_map[part]["href"]
                                body_parts.append('<div class="illust"><img src="' + href + '" alt="illustration"/></div>')
                            else:
                                lines = [html.escape(x) for x in part.split("\n") if x.strip()]
                                if lines:
                                    body_parts.append("<p>" + "<br/>".join(lines) + "</p>")
                    else:
                        lines = [html.escape(x) for x in b.split("\n") if x.strip()]
                        if lines:
                            body_parts.append("<p>" + "<br/>".join(lines) + "</p>")
            if chapters:
                for idx, ch in enumerate(chapters, start=1):
                    ch_title = ch.get("title") or f"第{idx}话"
                    body_parts.append('<h2 id="ch' + str(idx) + '">' + html.escape(str(ch_title)) + "</h2>")
                    render_text(ch.get("text") or "")
            else:
                render_text(text or "")
            body_html = "\n".join(body_parts) if body_parts else "<p></p>"
            meta_lines = [
                '    <dc:identifier id="BookId">' + safe_id + "</dc:identifier>",
                '    <dc:title>' + safe_title + "</dc:title>",
                '    <dc:creator>' + safe_author + "</dc:creator>",
                '    <dc:language>zh</dc:language>',
            ]
            if safe_desc:
                meta_lines.append("    <dc:description>" + safe_desc + "</dc:description>")
            if pub_date:
                meta_lines.append("    <dc:date>" + html.escape(str(pub_date)) + "</dc:date>")
            for t in tags:
                if t:
                    meta_lines.append("    <dc:subject>" + html.escape(str(t)) + "</dc:subject>")
            if cover_bytes:
                meta_lines.append('    <meta name="cover" content="cover-image"/>')
            meta_block = "\n".join(meta_lines)
            styles_css = "body{font-family:serif;line-height:1.6;}h1{margin:0.6em 0;}h2{margin:1em 0 0.4em;}p{margin:0.5em 0;} .meta{font-size:0.9em;color:#555;} .cover{display:flex;justify-content:center;align-items:center;height:100vh;} .illust{margin:1em 0;text-align:center;} img{max-width:100%;height:auto;}"
            content_xhtml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh" lang="zh">\n'
                "<head><title>" + safe_title + '</title><meta charset="utf-8"/><link rel="stylesheet" type="text/css" href="styles.css"/></head>\n'
                "<body>\n"
                "<h1>" + safe_title + "</h1>\n"
                '<div class="meta">' + (safe_author or "") + "</div>\n"
                + (f'<div class="meta">{safe_desc}</div>\n' if safe_desc else "")
                + (f'<div class="meta">标签：{", ".join([html.escape(str(t)) for t in tags])}</div>\n' if tags else "")
                + body_html
                + "\n</body>\n</html>\n"
            )
            manifest_items = [
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>',
                '    <item id="style" href="styles.css" media-type="text/css"/>'
            ]
            spine_items = []
            cover_xhtml = None
            cover_href = None
            cover_image_href = None
            if cover_bytes:
                cover_ext = cover_ext or ".jpg"
                cover_mime = cover_mime or mimetypes.types_map.get(cover_ext.lower(), "image/jpeg")
                cover_image_href = f"images/cover{cover_ext}"
                cover_href = "cover.xhtml"
                manifest_items.append('    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
                manifest_items.append(f'    <item id="cover-image" href="{cover_image_href}" media-type="{cover_mime}"/>')
                spine_items.append('    <itemref idref="cover"/>')
                cover_xhtml = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<!DOCTYPE html>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh" lang="zh">\n'
                    '<head><title>Cover</title><meta charset="utf-8"/><link rel="stylesheet" type="text/css" href="styles.css"/></head>\n'
                    '<body class="cover"><img src="' + cover_image_href + '" alt="cover"/></body>\n'
                    "</html>\n"
                )
            for idx, item in enumerate(inline_images, start=1):
                href = item.get("href")
                mime = item.get("mime")
                if href and mime:
                    manifest_items.append(f'    <item id="inline-{idx}" href="{href}" media-type="{mime}"/>')
            spine_items.append('    <itemref idref="content"/>')
            content_opf = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">\n'
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
                + meta_block + "\n"
                "  </metadata>\n"
                "  <manifest>\n"
                + "\n".join(manifest_items) + "\n"
                "  </manifest>\n"
                '  <spine toc="ncx">\n'
                + "\n".join(spine_items) + "\n"
                "  </spine>\n"
                "</package>\n"
            )
            nav_points = []
            play_order = 1
            if cover_href:
                nav_points.append(
                    '    <navPoint id="navPoint-' + str(play_order) + '" playOrder="' + str(play_order) + '">\n'
                    "      <navLabel><text>封面</text></navLabel>\n"
                    '      <content src="' + cover_href + '"/>\n'
                    "    </navPoint>"
                )
                play_order += 1
            nav_points.append(
                '    <navPoint id="navPoint-' + str(play_order) + '" playOrder="' + str(play_order) + '">\n'
                "      <navLabel><text>" + safe_title + "</text></navLabel>\n"
                '      <content src="content.xhtml"/>\n'
                "    </navPoint>"
            )
            play_order += 1
            if chapters:
                for idx, ch in enumerate(chapters, start=1):
                    ch_title = html.escape(str(ch.get("title") or f"第{idx}话"))
                    nav_points.append(
                        '    <navPoint id="navPoint-' + str(play_order) + '" playOrder="' + str(play_order) + '">\n'
                        "      <navLabel><text>" + ch_title + "</text></navLabel>\n"
                        '      <content src="content.xhtml#ch' + str(idx) + '"/>\n'
                        "    </navPoint>"
                    )
                    play_order += 1
            toc_ncx = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                "  <head>\n"
                '    <meta name="dtb:uid" content="' + safe_id + '"/>\n'
                '    <meta name="dtb:depth" content="1"/>\n'
                '    <meta name="dtb:totalPageCount" content="0"/>\n'
                '    <meta name="dtb:maxPageNumber" content="0"/>\n'
                "  </head>\n"
                "  <docTitle><text>" + safe_title + "</text></docTitle>\n"
                "  <navMap>\n"
                + "\n".join(nav_points) + "\n"
                "  </navMap>\n"
                "</ncx>\n"
            )
            container_xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                "  <rootfiles>\n"
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                "  </rootfiles>\n"
                "</container>\n"
            )
            with zipfile.ZipFile(output_path, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                zf.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr("OEBPS/toc.ncx", toc_ncx, compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr("OEBPS/styles.css", styles_css, compress_type=zipfile.ZIP_DEFLATED)
                if cover_xhtml and cover_image_href and cover_bytes:
                    zf.writestr("OEBPS/cover.xhtml", cover_xhtml, compress_type=zipfile.ZIP_DEFLATED)
                    zf.writestr(f"OEBPS/{cover_image_href}", cover_bytes, compress_type=zipfile.ZIP_DEFLATED)
                for item in inline_images:
                    href = item.get("href")
                    data = item.get("bytes")
                    if href and data:
                        zf.writestr(f"OEBPS/{href}", data, compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr("OEBPS/content.xhtml", content_xhtml, compress_type=zipfile.ZIP_DEFLATED)
            return True
        except Exception as e:
            self._set_last_error(f"EPUB 生成失败: {e}")
            return False
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

    def _download_novel_series(self, series_url: str, save_dir: Path) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
        m = re.search(r"/novel/series/(\d+)", series_url)
        if not m:
            self._set_last_error("系列ID解析失败")
            return None, None
        sid = m.group(1)
        series_info = self._get_json_with_backoff(f"{self.base_url}/ajax/novel/series/{sid}", timeout=20)
        if not series_info or series_info.get("error"):
            if series_info and series_info.get("error"):
                self._set_last_error(series_info.get("message") or "系列信息接口返回错误")
            return None, None
        series_body = series_info.get("body", {}) if isinstance(series_info, dict) else {}
        series_title = series_body.get("title") or series_body.get("seriesTitle") or f"series_{sid}"
        series_title = clean_filename(series_title)
        author = series_body.get("userName") or series_body.get("user_name") or ""
        description = description_to_text(series_body.get("description") or series_body.get("caption") or "")
        pub_date = series_body.get("updateDate") or series_body.get("lastUpdated") or series_body.get("updateAt") or ""
        tags = self._extract_tags(series_body)
        cover_url = series_body.get("coverUrl") or series_body.get("coverURL") or series_body.get("cover_url")
        cover_bytes = None
        cover_ext = None
        cover_mime = None
        if cover_url:
            cover_bytes, cover_ext, cover_mime = self._download_binary(cover_url, 20, True)
        titles_info = self._get_json_with_backoff(f"{self.base_url}/ajax/novel/series/{sid}/content_titles", timeout=20)
        if not titles_info or titles_info.get("error"):
            if titles_info and titles_info.get("error"):
                self._set_last_error(titles_info.get("message") or "系列章节接口返回错误")
            return None, None
        items = titles_info.get("body", []) if isinstance(titles_info, dict) else []
        if not items:
            self._set_last_error("系列章节为空")
            return None, None
        chapters = []
        inline_images = []
        for idx, item in enumerate(items, start=1):
            nid = item.get("id")
            if not nid:
                continue
            data = self._get_json_with_backoff(f"{self.base_url}/ajax/novel/{nid}", timeout=20)
            if not data or data.get("error"):
                continue
            body = data.get("body", {}) if isinstance(data, dict) else {}
            if cover_bytes is None:
                fallback_cover = body.get("coverUrl") or body.get("coverURL") or body.get("cover_url")
                if not fallback_cover:
                    imgs = self._extract_novel_images(body)
                    if imgs:
                        fallback_cover = next(iter(imgs.values()))
                if fallback_cover:
                    cover_bytes, cover_ext, cover_mime = self._download_binary(fallback_cover, 20, True)
            text = body.get("text") or body.get("content") or body.get("novelText") or ""
            if not text:
                text = description_to_text(body.get("description") or body.get("caption") or "")
            ch_title = item.get("title") or body.get("title") or f"第{idx}话"
            serial = item.get("serial")
            if serial and serial not in str(ch_title):
                ch_title = f"{serial} {ch_title}"
            text, ch_images = self._build_inline_images(text, body, f"{idx}_")
            inline_images.extend(ch_images)
            chapters.append({"title": ch_title, "text": text})
        if not chapters:
            self._set_last_error("系列章节下载失败")
            return None, None
        out = self._unique_path(save_dir, series_title, ".epub")
        meta = {
            "title": series_title,
            "author": author,
            "description": description,
            "tags": tags,
            "date": pub_date
        }
        if not self._write_epub("", meta, sid, out, cover_bytes, cover_ext, cover_mime, inline_images, chapters):
            return None, None
        return out, {"type": "novel_series", "id": sid, "title": series_title, "author": author}

    def download(self, work_url: str, save_dir: Optional[Path] = None) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
        if save_dir is None:
            root = get_project_root()
            path = Path(self.download_file_path)
            save_dir = path if path.is_absolute() else root / path
        save_dir.mkdir(parents=True, exist_ok=True)
        if "/novel/series/" in work_url:
            return self._download_novel_series(work_url, save_dir)
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
            text, inline_images = self._build_inline_images(text, body)
            out = self._unique_path(save_dir, title, ".epub")
            tags = self._extract_tags(body)
            description = description_to_text(body.get("description") or body.get("caption") or "")
            author = info.get("author") or body.get("userName") or body.get("user_name") or ""
            pub_date = body.get("createDate") or body.get("uploadDate") or body.get("published") or ""
            cover_url = body.get("coverUrl") or body.get("coverURL") or body.get("cover_url")
            cover_bytes = None
            cover_ext = None
            cover_mime = None
            if cover_url:
                cover_bytes, cover_ext, cover_mime = self._download_binary(cover_url, 20, True)
            meta = {
                "title": title,
                "author": author,
                "description": description,
                "tags": tags,
                "date": pub_date
            }
            if not self._write_epub(text, meta, nid, out, cover_bytes, cover_ext, cover_mime, inline_images):
                return None, None
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
            self._rate_limit_image()
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

    def get_author_info(self, url: str) -> Optional[tuple[str, int]]:
        """
        判断是否为作者主页，如果是则返回 (作者名, 作品数)。
        """
        if "/users/" not in url:
            return None
            
        m = re.search(r"/users/(\d+)", url)
        if not m:
            return None
            
        uid = m.group(1)
        name = self.get_user_name(uid)
        if not name:
            return None
            
        works = self.get_user_works(uid)
        return name, len(works)

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
                works = [url]
                
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
                                        if stats["failed"] > 0:
                                            stats["failed"] -= 1
                                        elif stats["skipped"] > 0:
                                            stats["skipped"] -= 1
        
        print(f"\n� 搬运大功告成啦: 🎉 成功 {stats['success']} | 💨 跳过 {stats['skipped']} | 💢 失败 {stats['failed']}")
        return stats
        
    def _download_worker(self, work_url: str, stats: Dict[str, int], lock: threading.Lock, pbar: tqdm, count_stats: bool):
        """
        线程工作函数，包含额外的重试逻辑
        """
        # Random delay to avoid burst requests
        time.sleep(random.uniform(0.1, 0.4))
        
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
                        if "已存在" in reason or "exists" in reason.lower():
                            pbar.write(f"💨 已跳过: {work_url} | 原因: {reason}")
                            if count_stats:
                                stats["skipped"] += 1
                        else:
                            pbar.write(f"💢 搬运失败: {work_url} | 原因: {reason}")
                            if count_stats:
                                stats["failed"] += 1
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
