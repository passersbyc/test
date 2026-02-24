class KemonoDownloader:
    """
    Kemono 下载器类。
    利用 config.json 中的 cookie 模拟网页版请求，下载 Kemono 上的内容。
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

        

        