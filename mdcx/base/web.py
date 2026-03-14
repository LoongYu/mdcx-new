#!/usr/bin/env python3
import asyncio
import re
import threading
from io import BytesIO
from pathlib import Path
from typing import Literal, overload

import aiofiles.os
from lxml import etree
from PIL import Image
from ping3 import ping

from ..config.manager import manager
from ..manual import ManualConfig
from ..models.log_buffer import LogBuffer
from ..signals import signal
from ..utils import executor
from ..utils.file import check_pic_async
from .web_sync import get_json_sync


@overload
async def check_url(url: str, length: Literal[False] = False, real_url: bool = False) -> str | None: ...
@overload
async def check_url(url: str, length: Literal[True] = True, real_url: bool = False) -> int | None: ...
async def check_url(url: str, length: bool = False, real_url: bool = False):
    """
    检测下载链接. 失败时返回 None.

    Args:
        url (str): 要检测的 URL
        length (bool, optional): 是否返回文件大小. Defaults to False.
        real_url (bool, optional): 直接返回真实 URL 不进行后续检查. Defaults to False.
    """
    if not url:
        return

    if "http" not in url:
        signal.add_log(f"🔴 检测链接失败: 格式错误 {url}")
        return

    try:
        # 使用 request 方法发送 HEAD 请求
        response, error = await manager.computed.async_client.request("HEAD", url)

        # 处理请求失败的情况
        if response is None:
            signal.add_log(f"🔴 检测链接失败: {error}")
            return

        # 不输出获取 dmm预览视频(trailer) 最高分辨率的测试结果到日志中
        if response.status_code == 404 and "_w.mp4" in url:
            return

        # 返回重定向的url
        true_url = str(response.url)
        if real_url:
            return true_url

        # 检查是否需要登录
        if "login" in true_url:
            signal.add_log(f"🔴 检测链接失败: 需登录 {true_url}")
            return

        # 检查是否带有图片不存在的关键词
        bad_url_keys = ["now_printing", "nowprinting", "noimage", "nopic", "media_violation"]
        for each_key in bad_url_keys:
            if each_key in true_url:
                signal.add_log(f"🔴 检测链接失败: 图片已被网站删除 {url}")
                return

        # 获取文件大小
        content_length = response.headers.get("Content-Length")
        if not content_length:
            # 如果没有获取到文件大小，尝试下载数据
            content, error = await manager.computed.async_client.get_content(true_url)

            if content is not None and len(content) > 0:
                signal.add_log(f"✅ 检测链接通过: 预下载成功 {true_url}")
                return 10240 if length else true_url
            else:
                signal.add_log(f"🔴 检测链接失败: 未返回大小且预下载失败 {true_url}")
                return
        # 如果返回内容的文件大小 < 8k，视为不可用
        elif int(content_length) < 8192:
            signal.add_log(f"🔴 检测链接失败: 返回大小({content_length}) < 8k {true_url}")
            return

        signal.add_log(f"✅ 检测链接通过: 返回大小({content_length}) {true_url}")
        return int(content_length) if length else true_url

    except Exception as e:
        signal.add_log(f"🔴 检测链接失败: 未知异常 {e} {url}")
        return


async def get_avsox_domain() -> str:
    issue_url = "https://tellme.pw/avsox"
    response, error = await manager.computed.async_client.get_text(issue_url)
    domain = "https://avsox.com"
    if response is not None:
        res = re.findall(r'(https://[^"]+)', response)
        for s in res:
            if s and "api.qrserver.com" not in s:
                return s
    return domain


async def get_amazon_data(req_url: str) -> tuple[bool, str]:
    """
    获取 Amazon 数据
    """
    headers = {
        "accept-encoding": "gzip, deflate, br",
        "Host": "www.amazon.co.jp",
    }
    html_info, error = await manager.computed.async_client.get_text(req_url, encoding="Shift_JIS")
    if html_info is None:
        html_info, error = await manager.computed.async_client.get_text(req_url, headers=headers, encoding="Shift_JIS")
    if html_info is None:
        session_id = ""
        ubid_acbjp = ""
        if x := re.findall(r'sessionId: "([^"]+)', html_info or ""):
            session_id = x[0]
        if x := re.findall(r"ubid-acbjp=([^ ]+)", html_info or ""):
            ubid_acbjp = x[0]
        headers_o = {
            "cookie": f"session-id={session_id}; ubid_acbjp={ubid_acbjp}",
        }
        headers.update(headers_o)
        html_info, error = await manager.computed.async_client.get_text(req_url, headers=headers, encoding="Shift_JIS")
    if html_info is None:
        return False, error
    if "HTTP 503" in html_info:
        headers = {"Host": "www.amazon.co.jp"}
        html_info, error = await manager.computed.async_client.get_text(req_url, headers=headers, encoding="Shift_JIS")
    if html_info is None:
        return False, error
    return True, html_info


async def get_imgsize(url) -> tuple[int, int]:
    response, _ = await manager.computed.async_client.request("GET", url, stream=True)
    if response is None or response.status_code != 200:
        return 0, 0
    file_head = BytesIO()
    chunk_size = 1024 * 10
    try:
        for chunk in response.iter_content(chunk_size):
            file_head.write(await chunk)
            try:

                def _get_size():
                    with Image.open(file_head) as img:
                        return img.size

                return await asyncio.to_thread(_get_size)
            except Exception:
                # 如果解析失败，继续下载更多数据
                continue
    except Exception:
        return 0, 0
    finally:
        response.close()

    return 0, 0


async def get_dmm_trailer(trailer_url: str) -> str:
    """
    尝试获取 dmm 最高分辨率预告片.

    Returns:
        str: 有效的最高分辨率预告片 URL.
    """
    # 如果不是DMM域名或已经是最高分辨率，则直接返回
    if ".dmm.co" not in trailer_url or "_mhb_w.mp4" in trailer_url:
        return trailer_url

    # 将相对URL转换为绝对URL
    if trailer_url.startswith("//"):
        trailer_url = "https:" + trailer_url

    """
    DMM预览片分辨率对应关系:
    '_sm_w.mp4': 320*180, 3.8MB     # 最低分辨率
    '_dm_w.mp4': 560*316, 10.1MB    # 中等分辨率
    '_dmb_w.mp4': 720*404, 14.6MB   # 次高分辨率
    '_mhb_w.mp4': 720*404, 27.9MB   # 最高分辨率
    
    示例:
    https://cc3001.dmm.co.jp/litevideo/freepv/s/ssi/ssis00090/ssis00090_sm_w.mp4
    https://cc3001.dmm.co.jp/litevideo/freepv/s/ssi/ssis00090/ssis00090_dm_w.mp4
    https://cc3001.dmm.co.jp/litevideo/freepv/s/ssi/ssis00090/ssis00090_dmb_w.mp4
    https://cc3001.dmm.co.jp/litevideo/freepv/s/ssi/ssis00090/ssis00090_mhb_w.mp4
    """

    match = re.search(r"(.+)(_[sd]mb?_w.mp4)", trailer_url)
    if not match:
        return trailer_url
    base_url, resolution_tag = match.groups()
    suffixes = ("_sm_w.mp4", "_dm_w.mp4", "_dmb_w.mp4", "_mhb_w.mp4")

    for i, suffix in enumerate(suffixes):
        if suffix in resolution_tag:
            for suf in suffixes[i + 1 :: -1]:
                if await check_url(base_url + suf):
                    return base_url + suf

    return trailer_url


def _ping_host_thread(host_address: str, result_list: list[int | None], i: int) -> None:
    response = ping(host_address, timeout=1)
    result_list[i] = int(response * 1000) if response else 0


# todo 可以移除 ping, 仅靠 http request 检测网络连通性
def ping_host(host_address: str) -> str:
    count = manager.config.retry
    result_list: list[int | None] = [None] * count
    thread_list: list[threading.Thread] = [None] * count  # type: ignore
    for i in range(count):
        thread_list[i] = threading.Thread(target=_ping_host_thread, args=(host_address, result_list, i))
        thread_list[i].start()
    for i in range(count):
        thread_list[i].join()
    new_list = [each for each in result_list if each]
    return (
        f"  ⏱ Ping {int(sum(new_list) / len(new_list))} ms ({len(new_list)}/{count})"
        if new_list
        else f"  🔴 Ping - ms (0/{count})"
    )


def check_version() -> int | None:
    if manager.config.update_check:
        url = "https://api.github.com/repos/sqzw-x/mdcx/releases/latest"
        res_json, error = get_json_sync(url)
        if res_json is not None:
            try:
                latest_version = res_json["tag_name"]
                latest_version = int(latest_version)
                return latest_version
            except Exception:
                signal.add_log(f"❌ 获取最新版本失败！{res_json}")
    return None


def check_theporndb_api_token() -> str:
    tips = "✅ 连接正常! "
    api_token = manager.config.theporndb_api_token
    url = "https://api.theporndb.net/scenes/hash/8679fcbdd29fa735"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if not api_token:
        tips = "❌ 未填写 API Token，影响欧美刮削！可在「设置」-「网络」添加！"
    else:
        response, err = executor.run(manager.computed.async_client.request("GET", url, headers=headers))
        if response is None:
            tips = f"❌ ThePornDB 连接失败: {err}"
            signal.show_log_text(tips)
            return tips
        if response.status_code == 401 and "Unauthenticated" in str(response.text):
            tips = "❌ API Token 错误！影响欧美刮削！请到「设置」-「网络」中修改。"
        elif response.status_code == 200:
            tips = "✅ 连接正常！" if response.json().get("data") else "❌ 返回数据异常！"
        else:
            tips = f"❌ 连接失败！请检查网络或代理设置！ {response.status_code} {response.text}"
    signal.show_log_text(tips.replace("❌", " ❌ ThePornDB").replace("✅", " ✅ ThePornDB"))
    return tips


async def _get_pic_by_google(pic_url):
    google_keyused = manager.computed.google_keyused
    google_keyword = manager.computed.google_keyword
    req_url = f"https://www.google.com/searchbyimage?sbisrc=2&image_url={pic_url}"
    # req_url = f'https://lens.google.com/uploadbyurl?url={pic_url}&hl=zh-CN&re=df&ep=gisbubu'
    response, error = await manager.computed.async_client.get_text(req_url)
    big_pic = True
    if response is None:
        return "", (0, 0), False
    url_list = re.findall(r'a href="([^"]+isz:l[^"]+)">', response)
    url_list_middle = re.findall(r'a href="([^"]+isz:m[^"]+)">', response)
    if not url_list and url_list_middle:
        url_list = url_list_middle
        big_pic = False
    if url_list:
        req_url = "https://www.google.com" + url_list[0].replace("amp;", "")
        response, error = await manager.computed.async_client.get_text(req_url)
    if response is None:
        return "", (0, 0), False
    url_list = re.findall(r'\["(http[^"]+)",(\d{3,4}),(\d{3,4})\],[^[]', response)
    # 优先下载放前面
    new_url_list = []
    for each_url in url_list.copy():
        if int(each_url[2]) < 800:
            url_list.remove(each_url)

    for each_key in google_keyused:
        for each_url in url_list.copy():
            if each_key in each_url[0]:
                new_url_list.append(each_url)
                url_list.remove(each_url)
    # 只下载关时，追加剩余地址
    if "goo_only" not in [item.value for item in manager.config.download_hd_pics]:
        new_url_list += url_list
    # 解析地址
    for each in new_url_list:
        temp_url = each[0]
        for temp_keyword in google_keyword:
            if temp_keyword in temp_url:
                break
        else:
            h = int(each[1])
            w = int(each[2])
            if w > h and w / h < 1.4:  # thumb 被拉高时跳过
                continue

            p_url = temp_url.encode("utf-8").decode("unicode_escape")  # url中的Unicode字符转义，不转义，url请求会失败
            if "m.media-amazon.com" in p_url:
                p_url = re.sub(r"\._[_]?AC_[^\.]+\.", ".", p_url)
                pic_size = await get_imgsize(p_url)
                if pic_size[0]:
                    return p_url, pic_size, big_pic
            else:
                url = await check_url(p_url)
                if url:
                    pic_size = (w, h)
                    return url, pic_size, big_pic
    return "", (0, 0), False


async def get_big_pic_by_google(pic_url, poster=False) -> tuple[str, tuple[int, int]]:
    url, pic_size, big_pic = await _get_pic_by_google(pic_url)
    if not poster:
        if big_pic or (
            pic_size and int(pic_size[0]) > 800 and int(pic_size[1]) > 539
        ):  # cover 有大图时或者图片高度 > 800 时使用该图片
            return url, pic_size
        return "", (0, 0)
    if url and int(pic_size[1]) < 1000:  # poster，图片高度小于 1500，重新搜索一次
        url, pic_size, big_pic = await _get_pic_by_google(url)
    if pic_size and (
        big_pic or "blogger.googleusercontent.com" in url or int(pic_size[1]) > 560
    ):  # poster，大图或高度 > 560 时，使用该图片
        return url, pic_size
    else:
        return "", (0, 0)


async def get_actorname(number: str) -> tuple[bool, str]:
    # 获取真实演员名字
    url = f"https://av-wiki.net/?s={number}"
    res, error = await manager.computed.async_client.get_text(url)
    if res is None:
        return False, f"Error: {error}"
    html_detail = etree.fromstring(res, etree.HTMLParser(encoding="utf-8"))
    actor_box = html_detail.xpath('//ul[@class="post-meta clearfix"]')
    for each in actor_box:
        actor_name = each.xpath('li[@class="actress-name"]/a/text()')
        actor_number = each.xpath('li[@class="actress-name"]/following-sibling::li[last()]/text()')
        if actor_number and (
            actor_number[0].upper().endswith(number.upper()) or number.upper().endswith(actor_number[0].upper())
        ):
            return True, ",".join(actor_name)
    return False, "No Result!"


async def get_yesjav_title(movie_number: str) -> str:
    yesjav_url = f"http://www.yesjav101.com/search.asp?q={movie_number}&"
    movie_title = ""
    response, error = await manager.computed.async_client.get_text(yesjav_url)
    if response is not None:
        parser = etree.HTMLParser(encoding="utf-8")
        html = etree.HTML(response, parser)
        movie_title = html.xpath(
            '//dl[@id="zi"]/p/font/a/b[contains(text(), $number)]/../../a[contains(text(), "中文字幕")]/text()',
            number=movie_number,
        )
        if movie_title:
            movie_title = movie_title[0]
            for each in ManualConfig.CHAR_LIST:
                movie_title = movie_title.replace(each, "")
            movie_title = movie_title.strip()
    return movie_title


async def download_file_with_filepath(url: str, file_path: Path, folder_new_path: Path) -> bool:
    if not url:
        return False

    if not await aiofiles.os.path.exists(folder_new_path):
        await aiofiles.os.makedirs(folder_new_path)
    try:
        if await manager.computed.async_client.download(url, file_path):
            return True
    except Exception:
        pass
    LogBuffer.log().write(f"\n 🥺 Download failed! {url}")
    return False


async def download_extrafanart_task(task: tuple[str, Path, Path, str]) -> bool:
    extrafanart_url, extrafanart_file_path, extrafanart_folder_path, extrafanart_name = task
    if await download_file_with_filepath(extrafanart_url, extrafanart_file_path, extrafanart_folder_path):
        if await check_pic_async(extrafanart_file_path):
            return True
    else:
        LogBuffer.log().write(f"\n 💡 {extrafanart_name} download failed! ( {extrafanart_url} )")
    return False
