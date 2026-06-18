"""TuShare ci_daily data extractor."""

import json
import logging
import time
from pathlib import Path

import pandas as pd
import tushare as ts
from tenacity import retry, stop_after_attempt, wait_exponential

from stock_datasource.config.settings import settings
from stock_datasource.core.proxy import proxy_context

logger = logging.getLogger(__name__)


class CiDailyExtractor:
    """
    TuShare `ci_daily` 数据抽取器（中信行业指数日线行情）。

    这个类只负责“从 TuShare 拉取数据”，不负责：
    - 业务字段清洗/重命名（由插件的 transform_data 负责）
    - 表结构创建、缺口回补、入库（由插件/流水线负责）

    关键能力：
    - Token 初始化与 Pro API 客户端创建（读取 settings.TUSHARE_TOKEN）
    - 速率控制（rate_limit / min_interval），避免触发 TuShare 访问频控
    - 重试（tenacity 指数退避），提升网络抖动/临时错误时的成功率
    - 代理上下文（proxy_context），在需要代理访问时自动启用

    返回值约定：
    - 所有对外方法都返回 pandas.DataFrame
    - 若 API 返回 None，则统一返回空 DataFrame（而不是 None）
    """

    def __init__(self):
        # 从全局配置读取 TuShare Token；没有 Token 时直接失败，避免后续静默空数据
        self.token = settings.TUSHARE_TOKEN

        # 插件内 config.json 用于控制调用速率与超时时间等参数
        config_file = Path(__file__).parent / "config.json"
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        self.rate_limit = config.get("rate_limit", 500)
        self.timeout = config.get("timeout", 30)

        if not self.token:
            raise ValueError("TUSHARE_TOKEN not configured")

        # 初始化 TuShare Pro 客户端
        ts.set_token(self.token)
        try:
            # 新版 tushare 支持在 pro_api 里设置超时
            self.pro = ts.pro_api(timeout=self.timeout)
        except TypeError:
            # 兼容旧版 tushare：不支持 timeout 参数时退化为默认构造
            self.pro = ts.pro_api()

        # 速率控制的时间戳与最小间隔（秒） 每分钟最多调用 self.rate_limit 次
        self._last_call_time = 0
        self._min_interval = 60.0 / self.rate_limit

    def _rate_limit(self):
        """
        以“最小间隔”方式做简单限流。

        `rate_limit` 是“每分钟最大调用数”，这里换算成每次调用之间至少间隔多少秒。
        """
        current_time = time.time()
        time_since_last = current_time - self._last_call_time
        if time_since_last < self._min_interval:
            time.sleep(self._min_interval - time_since_last)
        self._last_call_time = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        reraise=True,
    )
    def _call_api(self, **kwargs) -> pd.DataFrame:
        """
        统一的 API 调用入口。

        - 先限流（_rate_limit）
        - 在 proxy_context 中调用 self.pro.ci_daily(**kwargs)
        - tenacity 装饰器负责重试：
          - 最多 3 次
          - 指数退避，等待区间 [4s, 10s]

        kwargs 直接透传给 TuShare `ci_daily`，常见参数：
        - trade_date: str, 交易日期 YYYYMMDD
        - ts_code: str, 指数代码（可选）
        - start_date / end_date: str, 日期区间 YYYYMMDD
        """
        self._rate_limit()
        try:
            # proxy_context 会按项目配置决定是否启用代理；对调用方透明
            with proxy_context():
                result = self.pro.ci_daily(**kwargs)
            return result if result is not None else pd.DataFrame()
        except Exception as e:
            # 记录错误后抛出，让 tenacity 捕获并决定是否重试
            logger.error(f"API call failed: {e}")
            raise

    def extract(self, trade_date: str, ts_code: str | None = None) -> pd.DataFrame:
        """
        按“单个交易日”抽取。

        Args:
            trade_date: 交易日，格式 YYYYMMDD
            ts_code: 可选，指定中信行业指数代码；不传则返回该日所有指数
        """
        kwargs = {"trade_date": trade_date}
        if ts_code:
            kwargs["ts_code"] = ts_code
        return self._call_api(**kwargs)

    def extract_by_date_range(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        按“指数代码 + 日期区间”抽取。

        Args:
            ts_code: 中信行业指数代码
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD
        """
        return self._call_api(ts_code=ts_code, start_date=start_date, end_date=end_date)


extractor = CiDailyExtractor()
