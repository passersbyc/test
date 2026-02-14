import json
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs
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
        self._load_config()

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
            
        except Exception as e:
            print(f"加载配置失败: {e}")
            # 设置默认 URL 以防万一
            self.base_url = "https://www.pixiv.net"
            self.ajax_url = "https://www.pixiv.net/ajax/illust"

    def get_info(self, work_url: str) -> Optional[Dict[str, Any]]:
        try:
            if "/artworks/" in work_url:
                m = re.search(r"/artworks/(\d+)", work_url)
                if not m:
                    return None
                pid = m.group(1)
                r = self.session.get(f"{self.ajax_url}/{pid}", timeout=15)
                r.raise_for_status()
                j = r.json()
                if j.get("error"):
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
                    return None
                j = self.session.get(f"{self.base_url}/ajax/novel/{nid}", timeout=15)
                j.raise_for_status()
                body = j.json().get("body", {}) if isinstance(j.json(), dict) else {}
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
                    h = self.session.get(work_url, timeout=15)
                    h.raise_for_status()
                    text = h.text
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
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            d = r.json()
            if d.get("error"):
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
            return []

    def download(self, work_url: str, save_dir: Optional[Path] = None) -> Optional[Path]:
        if save_dir is None:
            save_dir = get_project_root() / "downloads"
        save_dir.mkdir(parents=True, exist_ok=True)
        info = self.get_info(work_url)
        if not info:
            return None
        title = info.get("title") or "untitled"
        title = clean_filename(title)
        if "/novel/show.php" in work_url:
            p = urlparse(work_url)
            nid = parse_qs(p.query).get("id", [None])[0]
            if not nid:
                return None
            r = self.session.get(f"{self.base_url}/ajax/novel/{nid}", timeout=20)
            r.raise_for_status()
            body = r.json().get("body", {}) if isinstance(r.json(), dict) else {}
            text = body.get("text") or body.get("content") or body.get("novelText") or ""
            if not text:
                h = self.session.get(work_url, timeout=20)
                h.raise_for_status()
                text = description_to_text(info.get("description") or "")
            out = self._unique_path(save_dir, title, ".txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            return out
        if "/artworks/" in work_url:
            m = re.search(r"/artworks/(\d+)", work_url)
            if not m:
                return None
            pid = m.group(1)
            urls = self.get_illust_pages(pid)
            if not urls:
                return None
            tmp_dir = save_dir / f"{title}__{pid}"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            for u in urls:
                fn = u.split("/")[-1]
                fp = tmp_dir / fn
                if fp.exists():
                    continue
                if not self._download_file(u, fp):
                    return None
            pdf_path = convert_images_to_book(tmp_dir, target_format="pdf", delete_original=True)
            final = self._unique_path(save_dir, title, ".pdf")
            pdf_path.rename(final)
            return final
        return None
    def _download_file(self, url: str, save_path: Path) -> bool:
        try:
            with self.session.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(save_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return True
        except Exception:
            return False
    def import_download(self, file_path: Path, work_url: Optional[str] = None) -> Optional[Path]:
        if not file_path.exists():
            return None
        if not work_url:
            return None
        info = self.get_info(work_url)
        if not info:
            return None
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
            return None
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
        return target_path
    def download_and_import(self, work_url: str) -> Optional[Path]:
        root = get_project_root()
        csv_path = root / "library_manifest.csv"
        if csv_path.exists():
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    src = row.get("来源") or row.get("source")
                    if src and src.strip() == work_url.strip():
                        return None
        dl = self.download(work_url)
        if not dl:
            return None
        return self.import_download(dl, work_url)

    

if __name__ == "__main__":
    d = PixivDownloader()
    u1 = "https://www.pixiv.net/artworks/141134416"
    u2 = "https://www.pixiv.net/novel/show.php?id=25410727"
    r1 = d.download_and_import(u1)
    r2 = d.download_and_import(u2)
    print(str(r1) if r1 else "artworks 141134416 skipped or failed")
    print(str(r2) if r2 else "novel 25410727 skipped or failed")
