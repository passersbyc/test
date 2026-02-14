import json
import re
import time
import requests
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

# 将项目根目录添加到 sys.path 以便导入模块
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from toolboxs import get_project_root, clean_filename, convert_images_to_book, html_to_text

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

    def get_info(self, url: str) -> Optional[Dict[str, Any]]:
        """
        根据作品网址获取详细信息。
        支持插画 (artworks), 小说 (novel), 小说系列 (novel/series)。
        
        :param url: 作品网址
        :return: 包含 author, series, title, type, tags 等信息的字典
        """
        pid = None
        url_type = None
        
        # 1. 匹配插画/漫画: https://www.pixiv.net/artworks/123456
        if "artworks" in url:
            match = re.search(r"artworks/(\d+)", url)
            if match:
                pid = match.group(1)
                url_type = "illust"
                
        # 2. 匹配小说: https://www.pixiv.net/novel/show.php?id=123456
        elif "novel/show.php" in url:
            match = re.search(r"id=(\d+)", url)
            if match:
                pid = match.group(1)
                url_type = "novel"
                
        # 3. 匹配小说系列: https://www.pixiv.net/novel/series/123456
        elif "novel/series" in url:
            match = re.search(r"series/(\d+)", url)
            if match:
                pid = match.group(1)
                url_type = "series"

        if not pid or not url_type:
            print(f"无法解析 URL: {url}")
            return None

        print(f"正在解析 {url_type} ID: {pid} ...")
        
        try:
            if url_type == "illust":
                return self._process_illust_info(pid)
            elif url_type == "novel":
                return self._process_novel_info(pid)
            elif url_type == "series":
                return self._process_series_info(pid)
        except Exception as e:
            print(f"获取信息失败: {e}")
            return None

    def _process_illust_info(self, pid: str) -> Dict[str, Any]:
        """处理插画信息"""
        # 使用 base_url 拼接，因为 self.ajax_url 可能只指向 illust
        api_url = f"{self.base_url}/ajax/illust/{pid}"
        data = self._fetch_json(api_url)
        
        tags = [tag.get("tag") for tag in data.get("tags", {}).get("tags", [])]
        
        return {
            "type": "illust" if data.get("illustType") == 0 else "manga", # 0: illust, 1: manga, 2: ugoira
            "id": data.get("id"),
            "title": clean_filename(data.get("title")),
            "description": html_to_text(data.get("description")),
            "author": {
                "id": data.get("userId"),
                "name": data.get("userName")
            },
            "tags": tags,
            "series": data.get("seriesNavData", {}).get("seriesType") if data.get("seriesNavData") else None, # 插画系列信息结构较复杂，暂取简单
            "page_count": data.get("pageCount"),
            "create_date": data.get("createDate"),
            "urls": data.get("urls", {})
        }

    def _process_novel_info(self, pid: str) -> Dict[str, Any]:
        """处理小说信息"""
        api_url = f"{self.base_url}/ajax/novel/{pid}"
        data = self._fetch_json(api_url)
        
        tags = [tag.get("tag") for tag in data.get("tags", {}).get("tags", [])]
        
        series_info = None
        if data.get("seriesNavData"):
            series_data = data.get("seriesNavData")
            series_info = {
                "id": series_data.get("seriesId"),
                "title": series_data.get("title"),
                "order": series_data.get("order")
            }

        return {
            "type": "novel",
            "id": data.get("id"),
            "title": clean_filename(data.get("title")),
            "description": html_to_text(data.get("description")),
            "author": {
                "id": data.get("userId"),
                "name": data.get("userName")
            },
            "tags": tags,
            "series": series_info,
            "page_count": data.get("pageCount"), # 小说通常指字数或页数概念
            "word_count": data.get("characterCount"),
            "create_date": data.get("createDate")
        }

    def _process_series_info(self, sid: str) -> Dict[str, Any]:
        """处理小说系列信息"""
        api_url = f"{self.base_url}/ajax/novel/series/{sid}"
        data = self._fetch_json(api_url)
        
        # Pixiv API 变动：tags 可能是字符串列表，也可能是字典列表
        raw_tags = data.get("tags", [])
        tags = []
        for tag in raw_tags:
            if isinstance(tag, dict):
                tags.append(tag.get("tag", ""))
            elif isinstance(tag, str):
                tags.append(tag)
        
        return {
            "type": "series",
            "id": data.get("id"),
            "title": clean_filename(data.get("title")),
            "description": html_to_text(data.get("caption")), # 系列简介通常是 caption
            "author": {
                "id": data.get("userId"),
                "name": data.get("userName") # 系列接口可能不直接返回 userName，需检查
            },
            "tags": tags,
            "series": None, # 本身就是系列
            "total_count": data.get("contentCount"), # 作品总数
            "create_date": data.get("createDate")
        }

    def _fetch_json(self, url: str) -> Dict[str, Any]:
        """通用 JSON 获取方法"""
        response = self.session.get(url, timeout=10)
        response.raise_for_status()
        res_json = response.json()
        if res_json.get("error"):
            raise ValueError(f"API Error: {res_json.get('message')}")
        return res_json.get("body", {})

    
if __name__ == "__main__":
    downloader = PixivDownloader()
    
    test_urls = [
        "https://www.pixiv.net/artworks/131096736",
        "https://www.pixiv.net/novel/series/14186154", 
        "https://www.pixiv.net/novel/show.php?id=27276178",
        "https://www.pixiv.net/artworks/141124557"
    ]
    
    print("-" * 50)
    for url in test_urls:
        print(f"测试 URL: {url}")
        info = downloader.get_info(url)
        if info:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        print("-" * 50)
