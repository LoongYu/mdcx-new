from ..config.manager import manager
from ..utils import executor


def get_text_sync(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    use_proxy=True,
    encoding: str = "utf-8",
):
    return executor.run(
        manager.computed.async_client.get_text(
            url, headers=headers, cookies=cookies, encoding=encoding, use_proxy=use_proxy
        )
    )


def request_sync(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    use_proxy=True,
    allow_redirects: bool = True,
):
    return executor.run(
        manager.computed.async_client.request(
            method,
            url,
            headers=headers,
            cookies=cookies,
            use_proxy=use_proxy,
            allow_redirects=allow_redirects,
        )
    )


def get_json_sync(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    use_proxy=True,
):
    return executor.run(
        manager.computed.async_client.get_json(url, headers=headers, cookies=cookies, use_proxy=use_proxy)
    )
