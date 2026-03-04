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
from types import SimpleNamespace

# 确保项目根目录在 sys.path 中，以便导入 src 模块
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.cli.downplugin.base import BaseDownloader, setup_logging
from toolboxs import (
    get_project_root,
    clean_filename,
    convert_images_to_book,
    description_to_text,
    logger # 直接复用 toolboxs 的 logger
)

# setup_logging("application.log") # 移除重复配置
# logger = logging.getLogger(__name__)

class PixivDownloader(BaseDownloader):
    """
    Pixiv 下载器类。
    利用 config.json 中的 cookie 模拟网页版请求，下载插画和漫画。
    """
    
    def __init__(self):
        """
        初始化下载器，加载配置和 Cookie。
        """
        super().__init__()
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
        self._load_existing_sources()
        self._load_config()
        self.download_file_path = "downloads"

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
            
    def download_and_import(self, work_url: str) -> tuple[Optional[Path], str]:
        self._clear_last_error()
        if self._is_source_in_manifest(work_url):
            return None, "清单已存在来源"
        
        dl, info = self.download(work_url)
        if not dl:
            return None, self._last_error or "下载失败"
        
        # 转换元数据
        metadata = {}
        if info:
            metadata["title"] = info.get("title")
            metadata["author"] = info.get("author")
            metadata["series"] = info.get("series")
            metadata["tags"] = info.get("tags") or []
        
        metadata["source"] = work_url

        res, reason = self.import_download(dl, work_url, metadata)
        if res:
            self.existing_sources.add(work_url.strip())
            return res, "ok"
        return None, reason

    def get_user_works(self, user_id: str) -> List[str]:
        """
        获取用户所有作品（插画、漫画、小说）的URL列表。
        """
        # 确保 user_id 只是数字 ID，而不是完整 URL
        if "users/" in user_id:
            m = re.search(r"/users/(\d+)", user_id)
            if m:
                user_id = m.group(1)
        
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

    def process_url(self, url: str | List[str]) -> Dict[str, int]:
        """
        处理输入URL，自动识别作品、用户或系列，并下载导入。
        支持单个 URL 字符串或 URL 列表。
        
        如果输入是列表，则默认所有条目均为作品链接，跳过解析步骤。
        
        返回统计信息: {"success": 0, "failed": 0, "skipped": 0}
        """
        stats = {"success": 0, "failed": 0, "skipped": 0}
        works = []
        
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
            
            # User Profile
            if "/users/" in u:
                m = re.search(r"/users/(\d+)", u)
                if m:
                    uid = m.group(1)
                    author_name = self.get_user_name(uid) or uid
                    print(f"🎨 发现了一位画师大大 (作者: {author_name})，正在翻阅作品集...")
                    works.extend(self.get_user_works(uid))
            
            # Novel Series
            elif "/novel/series/" in u:
                m = re.search(r"/novel/series/(\d+)", u)
                if m:
                    sid = m.group(1)
                    print(f"📚 发现了一套精彩的小说 (ID: {sid})，正在整理章节...")
                    works.extend(self.get_series_works(sid, is_novel=True))
                    
            # Illust/Manga Series (user/x/series/y)
            elif "/series/" in u:
                m = re.search(r"/series/(\d+)", u)
                if m:
                    sid = m.group(1)
                    print(f"🎨 发现了一套精美的插画 (ID: {sid})，正在整理画集...")
                    works.extend(self.get_series_works(sid, is_novel=False))
                    
            # Single Work
            elif "/artworks/" in u or "/novel/show.php" in u:
                works.append(u)
                
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
        
        # 使用多线程下载
        # 适当增加线程数，但保持在合理范围，配合重试策略
        max_workers = self.max_workers
        print(f"🚀 召唤 {max_workers} 只搬运小精灵 (Max Workers: {max_workers})")
        
        lock = threading.Lock()
        
        # 记录需要重试的 URL
        retry_urls = []
        
        with tqdm(total=total, unit="个", desc="📦 搬运进度", ncols=80, colour='MAGENTA') as pbar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                futures = {
                    executor.submit(self._download_worker, u, stats, lock, pbar): u
                    for u in works
                }
                
                # 处理完成的任务
                for future in concurrent.futures.as_completed(futures):
                    try:
                        work_url, ok, reason = future.result()
                        # 如果失败且包含 429 错误，加入重试列表
                        if not ok and reason and "429" in str(reason):
                             retry_urls.append(work_url)
                    except Exception as e:
                        logger.error(f"任务执行异常: {e}")

        # 如果有 429 错误，进行重试
        if retry_urls and self.retry_429:
            print(f"\n♻️ 休息 {self.retry_429_delay_seconds} 秒，准备重试 {len(retry_urls)} 个任务...")
            time.sleep(self.retry_429_delay_seconds)
            
            with tqdm(total=len(retry_urls), unit="个", desc="♻️ 限流重试", ncols=80, colour='MAGENTA') as rpbar:
                 with concurrent.futures.ThreadPoolExecutor(max_workers=self.retry_429_max_workers) as rex:
                    rfutures = {
                        rex.submit(self._download_worker, u, stats, lock, rpbar): u
                        for u in retry_urls
                    }
                    concurrent.futures.wait(rfutures)

        print(f"\n🎉 搬运大功告成啦！ 成功: {stats['success']} | 跳过: {stats['skipped']} | 失败: {stats['failed']}")
        return stats

        
    def _download_worker(self, work_url: str, stats: Dict[str, int], lock: threading.Lock, pbar: tqdm) -> tuple[str, bool, str]:
        """
        线程工作函数，包含额外的重试逻辑
        """
        # Random delay to avoid burst requests
        time.sleep(random.uniform(0.1, 0.4))
        
        max_retries = 3
        last_reason = ""
        
        for attempt in range(max_retries):
            try:
                res, reason = self.download_and_import(work_url)
                last_reason = reason
                
                if res:
                    with lock:
                        pbar.write(f"💖 搬运成功: {res.name}")
                        stats["success"] += 1
                    pbar.update(1)
                    return work_url, True, "ok"
                else:
                    if "已存在" in reason or "exists" in reason.lower() or "清单已存在" in reason:
                        with lock:
                            # 静默跳过，只计数不输出
                            # pbar.write(f"⏩ 已跳过: {work_url} (已经在库里啦)")
                            stats["skipped"] += 1
                        pbar.update(1)
                        return work_url, False, reason
                    
                    # 如果是 429，直接抛出异常以便外层捕获或继续重试
                    if "429" in reason:
                         raise requests.exceptions.RequestException("HTTP 429 Too Many Requests")

                    # 其他错误，继续重试
                    if attempt < max_retries - 1:
                        time.sleep(2 * (attempt + 1))
                        continue
                    
                    with lock:
                        pbar.write(f"💔 搬运失败: {work_url} | 原因: {reason}")
                        stats["failed"] += 1
                    pbar.update(1)
                    return work_url, False, reason

            except requests.exceptions.RequestException as e:
                last_reason = str(e)
                # 网络错误进行重试
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))  # 指数退避
                    continue
                
                with lock:
                    pbar.write(f"🌐 网络开了小差 {work_url}: {e}")
                    stats["failed"] += 1
                    pbar.update(1)
                return work_url, False, str(e)
                
            except Exception as e:
                last_reason = str(e)
                with lock:
                    pbar.write(f"💥 哎呀出错惹 {work_url}: {e}")
                    stats["failed"] += 1
                    pbar.update(1)
                return work_url, False, str(e)
        
        return work_url, False, last_reason

    

if __name__ == "__main__":
    d = PixivDownloader()
    # 示例用法:
    # 1. 下载单个作品
    # u1 = "https://www.pixiv.net/artworks/141134416"
    # d.process_url(u1)
    
    # 2. 下载用户所有作品 (示例用户)
    u_user = "https://www.pixiv.net/users/66902618"
    print(d.get_user_works(u_user))
    
    # 3. 下载系列作品
    # u_series = "https://www.pixiv.net/novel/series/123456" 
    # d.process_url(u_series)
