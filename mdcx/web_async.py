import asyncio
import random
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

import aiofiles
import httpx
from aiolimiter import AsyncLimiter
from curl_cffi import AsyncSession, Response
from curl_cffi.requests.exceptions import ConnectionError, RequestException, Timeout
from curl_cffi.requests.session import HttpMethod
from curl_cffi.requests.utils import not_set
from PIL import Image

_XOR_IMAGE_CDNS = {"imgwebsa.shzvsh.cn"}
_XOR_IMAGE_KEY = b"2019ysapp7527"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}


def _is_valid_image_bytes(content: bytes) -> bool:
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
        return True
    except Exception:
        return False


def _decode_xor_image_bytes(content: bytes, key: bytes = _XOR_IMAGE_KEY) -> bytes:
    data = bytearray(content)
    limit = min(100, len(data))
    for index in range(limit):
        data[index] ^= key[index % len(key)]
    return bytes(data)


def normalize_image_bytes(url: str, content: bytes | None) -> bytes | None:
    if not content:
        return None
    if _is_valid_image_bytes(content):
        return content
    try:
        host = httpx.URL(url).host or ""
    except Exception:
        host = ""
    if host in _XOR_IMAGE_CDNS:
        decoded = _decode_xor_image_bytes(content)
        if _is_valid_image_bytes(decoded):
            return decoded
    return None


class AsyncWebLimiters:
    def __init__(self):
        self.limiters: dict[str, AsyncLimiter] = {
            "127.0.0.1": AsyncLimiter(300, 1),
            "localhost": AsyncLimiter(300, 1),
        }

    def get(self, key: str, rate: float = 5, period: float = 1) -> AsyncLimiter:
        """默认对所有域名启用 5 req/s 的速率限制"""
        return self.limiters.setdefault(key, AsyncLimiter(rate, period))

    def remove(self, key: str):
        if key in self.limiters:
            del self.limiters[key]


class AsyncWebClient:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        retry: int = 3,
        timeout: float,
        log_fn: Callable[[str], None] | None = None,
        limiters: AsyncWebLimiters | None = None,
        loop=None,
    ):
        self.retry = retry
        self.proxy = proxy
        self.curl_session = AsyncSession(
            loop=loop,
            max_clients=50,
            verify=False,
            max_redirects=20,
            timeout=timeout,
            impersonate=random.choice(["chrome123", "chrome124", "chrome131", "chrome136", "firefox133", "firefox135"]),
        )

        self.log_fn = log_fn if log_fn is not None else lambda _: None
        self.limiters = limiters if limiters is not None else AsyncWebLimiters()

    def _prepare_headers(self, url: str | None = None, headers: dict[str, str] | None = None) -> dict[str, str]:
        """预处理请求头"""
        if not headers:
            headers = {}

        # 根据URL设置特定的Referer
        if url:
            if "getchu" in url:
                headers.update({"Referer": "http://www.getchu.com/top.html"})
            elif "xcity" in url:
                headers.update(
                    {"referer": "https://xcity.jp/result_published/?genre=%2Fresult_published%2F&q=2&sg=main&num=60"}
                )
            elif "javbus" in url:
                headers.update({"Referer": "https://www.javbus.com/"})
            elif "giga" in url and "cookie_set.php" not in url:
                headers.update({"Referer": "https://www.giga-web.jp/top.html"})

        return headers

    async def request(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
        data: dict[str, str] | list[tuple] | str | BytesIO | bytes | None = None,
        json_data: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> tuple[Response | None, str]:
        """
        执行请求的通用方法

        Args:
            url: 请求URL
            headers: 请求头
            cookies: cookies
            use_proxy: 是否使用代理
            data: 表单数据
            json_data: JSON数据
            timeout: 请求超时时间, 覆盖客户端默认值

        Returns:
            tuple[Optional[Response], str]: (响应对象, 错误信息)
        """
        try:
            u = httpx.URL(url)
            headers = self._prepare_headers(url, headers)
            await self.limiters.get(u.host).acquire()
            retry_count = self.retry
            error_msg = ""
            for attempt in range(retry_count):
                # 采用保守的重试策略, 除特定状态码外不进行重试
                retry = False
                try:
                    resp: Response = await self.curl_session.request(
                        method,
                        url,
                        proxy=self.proxy if use_proxy else None,
                        headers=headers,
                        cookies=cookies,
                        data=data,
                        json=json_data,
                        timeout=timeout or not_set,
                        stream=stream,
                        allow_redirects=allow_redirects,
                    )
                    # 检查响应状态:
                    # 对于 3xx 且带 Location 的响应，视为“可达”，由上层决定是否继续跟随重定向。
                    is_redirect_with_location = 300 <= resp.status_code < 400 and bool(resp.headers.get("Location"))
                    if resp.status_code >= 300 and not is_redirect_with_location:
                        error_msg = f"HTTP {resp.status_code}"
                        retry = resp.status_code in (
                            408,  # Request Timeout
                            429,  # Too Many Requests
                            504,  # Gateway Timeout
                        )
                    else:
                        self.log_fn(f"✅ {method} {url} 成功")
                        return resp, ""
                except Timeout:
                    error_msg = "连接超时"
                except ConnectionError as e:
                    error_msg = f"连接错误: {str(e)}"
                except RequestException as e:
                    error_msg = f"请求异常: {str(e)} {e.code}"
                except Exception as e:
                    error_msg = f"curl-cffi 异常: {str(e)}"
                if not retry:
                    break
                self.log_fn(f"🔴 {method} {url} 失败: {error_msg} ({attempt + 1}/{retry_count})")
                # 重试前等待
                if attempt < retry_count - 1:
                    await asyncio.sleep(attempt * 3 + 2)
            return None, f"{method} {url} 失败: {error_msg}"
        except Exception as e:
            error_msg = f"{method} {url} 未知错误:  {str(e)}"
            self.log_fn(f"🔴 {error_msg}")
            return None, error_msg

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        use_proxy: bool = True,
    ) -> tuple[str | None, str]:
        """请求文本内容"""
        resp, error = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        if resp is None:
            return None, error
        try:
            resp.encoding = encoding
            return resp.text, error
        except Exception as e:
            return None, f"文本解析失败: {str(e)}"

    async def get_content(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> tuple[bytes | None, str]:
        """请求二进制内容"""
        resp, error = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        if resp is None:
            return None, error

        return resp.content, ""

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> tuple[Any | None, str]:
        """请求JSON数据"""
        response, error = await self.request("GET", url, headers=headers, cookies=cookies, use_proxy=use_proxy)
        if response is None:
            return None, error
        try:
            return response.json(), ""
        except Exception as e:
            return None, f"JSON解析失败: {str(e)}"

    async def post_text(
        self,
        url: str,
        *,
        data: dict[str, str] | list[tuple] | str | BytesIO | bytes | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        encoding: str = "utf-8",
        use_proxy: bool = True,
    ) -> tuple[str | None, str]:
        """POST 请求, 返回响应文本内容"""
        response, error = await self.request(
            "POST", url, data=data, json_data=json_data, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        if response is None:
            return None, error
        try:
            response.encoding = encoding
            return response.text, ""
        except Exception as e:
            return None, f"文本解析失败: {str(e)}"

    async def post_json(
        self,
        url: str,
        *,
        data: dict[str, str] | list[tuple] | str | BytesIO | bytes | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> tuple[Any | None, str]:
        """POST 请求, 返回响应JSON数据"""
        response, error = await self.request(
            "POST", url, data=data, json_data=json_data, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        if error or response is None:
            return None, error

        try:
            return response.json(), ""
        except Exception as e:
            return None, f"JSON解析失败: {str(e)}"

    async def post_content(
        self,
        url: str,
        *,
        data: dict[str, str] | list[tuple] | str | BytesIO | bytes | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        use_proxy: bool = True,
    ) -> tuple[bytes | None, str]:
        """POST请求, 返回二进制响应"""
        response, error = await self.request(
            "POST", url, data=data, json_data=json_data, headers=headers, cookies=cookies, use_proxy=use_proxy
        )
        if error or response is None:
            return None, error

        return response.content, ""

    async def get_filesize(self, url: str, *, use_proxy: bool = True) -> int | None:
        """获取文件大小"""
        response, error = await self.request("HEAD", url, use_proxy=use_proxy)
        if response is None:
            self.log_fn(f"🔴 获取文件大小失败: {url} {error}")
            return None
        if response.status_code < 400:
            # 部分站点不返回标准 Content-Length；此处尽量解析，失败则降级为普通下载（不记错误）
            content_length = response.headers.get("Content-Length")
            if content_length:
                content_length = content_length.strip().replace(",", "")
                try:
                    return int(content_length)
                except (ValueError, TypeError):
                    pass

            content_range = response.headers.get("Content-Range")
            if content_range and "/" in content_range:
                total_size = content_range.rsplit("/", 1)[-1].strip()
                if total_size.isdigit():
                    return int(total_size)
            return None
        self.log_fn(f"🔴 获取文件大小失败: {url} HTTP {response.status_code}")
        return None

    async def download(self, url: str, file_path: Path, *, use_proxy: bool = True) -> bool:
        """
        下载文件. 当文件较大时分块下载

        Args:
            url: 下载链接
            file_path: 保存路径
            use_proxy: 是否使用代理

        Returns:
            bool: 下载是否成功
        """
        # 获取文件大小
        file_size = await self.get_filesize(url, use_proxy=use_proxy)
        # 判断是不是webp文件
        webp = False
        if file_path.suffix.lower() in {".jpg", ".jpeg"} and ".webp" in url:
            webp = True
        is_image_target = file_path.suffix.lower() in _IMAGE_SUFFIXES

        MB = 1024**2
        # 2 MB 以上使用分块下载, 不清楚为什么 webp 不分块, 可能是因为要转换成 jpg
        if file_size and file_size > 2 * MB and not webp:
            return await self._download_chunks(url, file_path, file_size, use_proxy)

        content, error = await self.get_content(url, use_proxy=use_proxy)
        if not content:
            self.log_fn(f"🔴 下载失败: {url} {error}")
            return False
        if is_image_target:
            normalized_content = normalize_image_bytes(url, content)
            if normalized_content is None:
                self.log_fn(f"🔴 图片解码失败: {url} {file_path}")
                return False
            content = normalized_content
        if not webp:
            try:
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(content)
                return True
            except Exception as e:
                self.log_fn(f"🔴 文件写入失败: {url} {file_path} {str(e)}")
                return False
        try:
            byte_stream = BytesIO(content)
            img: Image.Image = Image.open(byte_stream)
            if img.mode == "RGBA":
                img = img.convert("RGB")
            img.save(file_path, quality=95, subsampling=0)
            img.close()
            return True
        except Exception as e:
            self.log_fn(f"🔴 WebP转换失败: {url} {file_path} {str(e)}")
            return False

    async def _download_chunks(self, url: str, file_path: Path, file_size: int, use_proxy: bool = True) -> bool:
        """分块下载大文件"""
        # 分块，每块 1 MB
        MB = 1024**2
        each_size = min(1 * MB, file_size)
        parts = [(s, min(s + each_size, file_size)) for s in range(0, file_size, each_size)]

        self.log_fn(f"📦 分块下载: {url} {len(parts)} 个分块, 总大小: {file_size} bytes")

        # 先创建文件并预分配空间
        try:
            async with aiofiles.open(file_path, "wb") as f:
                await f.truncate(file_size)
        except Exception as e:
            self.log_fn(f"🔴 文件创建失败: {url} {str(e)}")
            return False

        # 创建下载任务
        semaphore = asyncio.Semaphore(10)  # 限制并发数
        tasks = []

        for i, (start, end) in enumerate(parts):
            task = self._download_chunk(semaphore, url, file_path, start, end, i, use_proxy)
            tasks.append(task)

        # 并发执行所有下载任务
        try:
            errors = await asyncio.gather(*tasks, return_exceptions=True)
            # 检查所有任务是否成功
            for i, err in enumerate(errors):
                if isinstance(err, Exception):
                    self.log_fn(f"🔴 分块 {i} 下载失败: {url} {str(err)}")
                    return False
                elif err:
                    self.log_fn(f"🔴 分块 {i} 下载失败: {url} {err}")
                    return False
            self.log_fn(f"✅ 多分块下载完成: {url} {file_path}")
            return True
        except Exception as e:
            self.log_fn(f"🔴 并发下载异常: {url} {str(e)}")
            return False

    async def _download_chunk(
        self,
        semaphore: asyncio.Semaphore,
        url: str,
        file_path: Path,
        start: int,
        end: int,
        chunk_id: int,
        use_proxy: bool = True,
    ) -> str | None:
        """下载单个分块"""
        async with semaphore:
            res, error = await self.request(
                "GET",
                url,
                headers={"Range": f"bytes={start}-{end}"},
                use_proxy=use_proxy,
                stream=True,
            )
            if res is None:
                return error
        # 写入文件
        async with aiofiles.open(file_path, "rb+") as fp:
            await fp.seek(start)
            await fp.write(await res.acontent())
        return ""
