"""
刮削过程的网络操作
"""

import asyncio
import re
import shutil
import time
import urllib.parse
from asyncio import to_thread
from pathlib import Path

import aiofiles
import aiofiles.os
from lxml import etree

from ..base.web import (
    check_url,
    download_extrafanart_task,
    download_file_with_filepath,
    get_amazon_data,
    get_big_pic_by_google,
    get_dmm_trailer,
    get_imgsize,
)
from ..config.enums import DownloadableFile, HDPicSource
from ..config.manager import manager
from ..models.flags import Flags
from ..models.log_buffer import LogBuffer
from ..models.types import CrawlersResult, OtherInfo
from ..signals import signal
from ..utils import convert_half, get_used_time, split_path
from ..utils.file import check_pic_async, copy_file_async, delete_file_async, move_file_async
from .image import cut_thumb_to_poster


async def get_big_pic_by_amazon(result: CrawlersResult, originaltitle_amazon: str, actor_amazon: list[str]) -> str:
    if not originaltitle_amazon or not actor_amazon:
        return ""
    hd_pic_url = ""
    originaltitle_amazon = re.sub(r"【.*】", "", originaltitle_amazon)
    originaltitle_amazon_list = [originaltitle_amazon]
    for originaltitle_amazon in originaltitle_amazon_list:
        # 需要两次urlencode，nb_sb_noss表示无推荐来源
        url_search = (
            "https://www.amazon.co.jp/black-curtain/save-eligibility/black-curtain?returnUrl=/s?k="
            + urllib.parse.quote_plus(urllib.parse.quote_plus(originaltitle_amazon.replace("&", " ") + " [DVD]"))
            + "&ref=nb_sb_noss"
        )
        success, html_search = await get_amazon_data(url_search)

        # 没有结果，尝试拆词，重新搜索
        if (
            not success
            or "キーワードが正しく入力されていても一致する商品がない場合は、別の言葉をお試しください。" in html_search
            and len(originaltitle_amazon_list) < 2
        ):
            for each_name in originaltitle_amazon.split(" "):
                if each_name not in originaltitle_amazon_list and (
                    len(each_name) > 8
                    or (not each_name.encode("utf-8").isalnum() and len(each_name) > 4)
                    and each_name not in actor_amazon
                ):
                    originaltitle_amazon_list.append(each_name)
            continue

        # 有结果时，检查结果
        if result and html_search:
            html = etree.fromstring(html_search, etree.HTMLParser())
            originaltitle_amazon_half = convert_half(originaltitle_amazon)
            originaltitle_amazon_half_no_actor = originaltitle_amazon_half

            # 标题缩短匹配（如无结果，则使用缩小标题再次搜索）
            if "検索に一致する商品はありませんでした。" in html_search and len(originaltitle_amazon_list) < 2:
                short_originaltitle_amazon = html.xpath(
                    '//div[@class="a-section a-spacing-base a-spacing-top-base"]/span[@class="a-size-base a-color-base"]/text()'
                )
                if short_originaltitle_amazon:
                    short_originaltitle_amazon = short_originaltitle_amazon[0].upper().replace(" DVD", "")
                    if short_originaltitle_amazon in originaltitle_amazon.upper():
                        originaltitle_amazon_list.append(short_originaltitle_amazon)
                        short_originaltitle_amazon = convert_half(short_originaltitle_amazon)
                        if short_originaltitle_amazon in originaltitle_amazon_half:
                            originaltitle_amazon_half = short_originaltitle_amazon
                for each_name in originaltitle_amazon.split(" "):
                    if each_name not in originaltitle_amazon_list and (
                        len(each_name) > 8
                        or (not each_name.encode("utf-8").isalnum() and len(each_name) > 4)
                        and each_name not in actor_amazon
                    ):
                        originaltitle_amazon_list.append(each_name)

            # 标题不带演员名匹配
            for each_actor in actor_amazon:
                originaltitle_amazon_half_no_actor = originaltitle_amazon_half_no_actor.replace(each_actor.upper(), "")

            # 检查搜索结果
            actor_result_list = set()
            title_result_list = []
            # s-card-container s-overflow-hidden aok-relative puis-wide-grid-style puis-wide-grid-style-t2 puis-expand-height puis-include-content-margin puis s-latency-cf-section s-card-border
            pic_card = html.xpath('//div[@class="a-section a-spacing-base"]')
            for each in pic_card:  # tek-077
                pic_ver_list = each.xpath(
                    'div//a[@class="a-size-base a-link-normal s-underline-text s-underline-link-text s-link-style a-text-bold"]/text()'
                )
                pic_title_list = each.xpath(
                    'div//h2[@class="a-size-base-plus a-spacing-none a-color-base a-text-normal"]/span/text()'
                )
                pic_url_list = each.xpath('div//div[@class="a-section aok-relative s-image-square-aspect"]/img/@src')
                detail_url_list = each.xpath('div//a[@class="a-link-normal s-no-outline"]/@href')
                if len(pic_ver_list) and len(pic_url_list) and (len(pic_title_list) and len(detail_url_list)):
                    pic_ver = pic_ver_list[0]  # 图片版本
                    pic_title = pic_title_list[0]  # 图片标题
                    pic_url = pic_url_list[0]  # 图片链接
                    detail_url = detail_url_list[0]  # 详情页链接（有时带有演员名）
                    if pic_ver in ["DVD", "Software Download"] and ".jpg" in pic_url:  # 无图时是.gif
                        pic_title_half = convert_half(re.sub(r"【.*】", "", pic_title))
                        pic_title_half_no_actor = pic_title_half
                        for each_actor in actor_amazon:
                            pic_title_half_no_actor = pic_title_half_no_actor.replace(each_actor, "")

                        # 判断标题是否命中
                        if (
                            originaltitle_amazon_half[:15] in pic_title_half
                            or originaltitle_amazon_half_no_actor[:15] in pic_title_half_no_actor
                        ):
                            detail_url = urllib.parse.unquote_plus(detail_url)
                            temp_title = re.findall(r"(.+)keywords=", detail_url)
                            temp_detail_url = (
                                temp_title[0] + pic_title_half if temp_title else detail_url + pic_title_half
                            )
                            url = re.sub(r"\._[_]?AC_[^\.]+\.", ".", pic_url)

                            # 判断演员是否在标题里，避免同名标题误匹配 MOPP-023
                            for each_actor in actor_amazon:
                                if each_actor in temp_detail_url:
                                    actor_result_list.add(url)
                                    if "写真付き" not in pic_title:  # NACR-206
                                        w, h = await get_imgsize(url)
                                        if w > 600 or not w:
                                            hd_pic_url = url
                                            return hd_pic_url
                                        else:
                                            result.poster = pic_url  # 用于 Google 搜图
                                            result.poster_from = "Amazon"
                                    break
                            else:
                                title_result_list.append([url, "https://www.amazon.co.jp" + detail_url])

            # 命中演员有多个结果时返回最大的（不等于1759/1758）
            if len(actor_result_list):
                pic_w = 0
                for each in actor_result_list:
                    new_pic_w, _ = await get_imgsize(each)
                    if new_pic_w > pic_w:
                        if new_pic_w >= 1770 or (1750 > new_pic_w > 600):  # 不要小图 FCDSS-001，截短的图（1758/1759）
                            pic_w = new_pic_w
                            hd_pic_url = each
                        else:
                            result.poster = each  # 用于 Google 搜图
                            result.poster_from = "Amazon"

                if hd_pic_url:
                    return hd_pic_url

            # 当搜索结果命中了标题，没有命中演员时，尝试去详情页获取演员信息
            elif (
                len(title_result_list) <= 20
                and "s-pagination-item s-pagination-next s-pagination-button s-pagination-separator" not in html_search
            ):
                for each in title_result_list[:4]:
                    try:
                        url_new = "https://www.amazon.co.jp" + re.findall(r"(/dp/[^/]+)", each[1])[0]
                    except Exception:
                        url_new = each[1]
                    success, html_detail = await get_amazon_data(url_new)
                    if success and html_detail:
                        html = etree.fromstring(html_detail, etree.HTMLParser())
                        detail_actor = str(html.xpath('//span[@class="author notFaded"]/a/text()')).replace(" ", "")
                        detail_info_1 = str(
                            html.xpath('//ul[@class="a-unordered-list a-vertical a-spacing-mini"]//text()')
                        ).replace(" ", "")
                        detail_info_2 = str(
                            html.xpath('//div[@id="detailBulletsWrapper_feature_div"]//text()')
                        ).replace(" ", "")
                        detail_info_3 = str(html.xpath('//div[@id="productDescription"]//text()')).replace(" ", "")
                        all_info = detail_actor + detail_info_1 + detail_info_2 + detail_info_3
                        for each_actor in actor_amazon:
                            if each_actor in all_info:
                                w, h = await get_imgsize(each[0])
                                if w > 720 or not w:
                                    return each[0]
                                else:
                                    result.poster = each[0]  # 用于 Google 搜图
                                    result.poster_from = "Amazon"

            # 有很多结果时（有下一页按钮），加演员名字重新搜索
            if (
                "s-pagination-item s-pagination-next s-pagination-button s-pagination-separator" in html_search
                or len(title_result_list) > 5
            ):
                amazon_orginaltitle_actor = result.amazon_orginaltitle_actor
                if amazon_orginaltitle_actor and amazon_orginaltitle_actor not in originaltitle_amazon:
                    originaltitle_amazon_list.append(f"{originaltitle_amazon} {amazon_orginaltitle_actor}")

    return hd_pic_url


async def trailer_download(
    result: CrawlersResult,
    folder_new: Path,
    folder_old: Path,
    naming_rule: str,
) -> bool | None:
    start_time = time.time()
    download_files = manager.config.download_files
    keep_files = manager.config.keep_files
    trailer_name = manager.config.trailer_simple_name
    result.trailer = await get_dmm_trailer(result.trailer)  # todo 或许找一个更合适的地方进行统一后处理
    trailer_url = result.trailer
    trailer_old_folder_path = folder_old / "trailers"
    trailer_new_folder_path = folder_new / "trailers"

    # 预告片名字不含视频文件名（只让一个视频去下载即可）
    if trailer_name:
        trailer_folder_path = folder_new / "trailers"
        trailer_file_name = "trailer.mp4"
        trailer_file_path = trailer_folder_path / trailer_file_name

        # 预告片文件夹已在已处理列表时，返回（这时只需要下载一个，其他分集不需要下载）
        if trailer_folder_path in Flags.trailer_deal_set:
            return
        Flags.trailer_deal_set.add(trailer_folder_path)

        # 不下载不保留时删除返回
        if DownloadableFile.TRAILER not in download_files and DownloadableFile.TRAILER not in keep_files:
            # 删除目标文件夹即可，其他文件夹和文件已经删除了
            if await aiofiles.os.path.exists(trailer_folder_path):
                await to_thread(shutil.rmtree, trailer_folder_path, ignore_errors=True)
            return

    else:
        # 预告片带文件名（每个视频都有机会下载，如果已有下载好的，则使用已下载的）
        trailer_file_name = naming_rule + "-trailer.mp4"
        trailer_folder_path = folder_new
        trailer_file_path = trailer_folder_path / trailer_file_name

        # 不下载不保留时删除返回
        if DownloadableFile.TRAILER not in download_files and DownloadableFile.TRAILER not in keep_files:
            # 删除目标文件，删除预告片旧文件夹、新文件夹（deal old file时没删除）
            if await aiofiles.os.path.exists(trailer_file_path):
                await delete_file_async(trailer_file_path)
            if await aiofiles.os.path.exists(trailer_old_folder_path):
                await to_thread(shutil.rmtree, trailer_old_folder_path, ignore_errors=True)
            if trailer_new_folder_path != trailer_old_folder_path and await aiofiles.os.path.exists(
                trailer_new_folder_path
            ):
                await to_thread(shutil.rmtree, trailer_new_folder_path, ignore_errors=True)
            return

    # 选择保留文件，当存在文件时，不下载。（done trailer path 未设置时，把当前文件设置为 done trailer path，以便其他分集复制）
    if DownloadableFile.TRAILER in keep_files and await aiofiles.os.path.exists(trailer_file_path):
        if not Flags.file_done_dic.get(result.number, {}).get("trailer"):
            Flags.file_done_dic[result.number].update({"trailer": trailer_file_path})
            # 带文件名时，删除掉新、旧文件夹，用不到了。（其他分集如果没有，可以复制第一个文件的预告片。此时不删，没机会删除了）
            if not trailer_name:
                if await aiofiles.os.path.exists(trailer_old_folder_path):
                    await to_thread(shutil.rmtree, trailer_old_folder_path, ignore_errors=True)
                if trailer_new_folder_path != trailer_old_folder_path and await aiofiles.os.path.exists(
                    trailer_new_folder_path
                ):
                    await to_thread(shutil.rmtree, trailer_new_folder_path, ignore_errors=True)
        LogBuffer.log().write(f"\n 🍀 Trailer done! (old)({get_used_time(start_time)}s) ")
        return True

    # 带文件名时，选择下载不保留，或者选择保留但没有预告片，检查是否有其他分集已下载或本地预告片
    # 选择下载不保留，当没有下载成功时，不会删除不保留的文件
    done_trailer_path = Flags.file_done_dic.get(result.number, {}).get("trailer")
    if not trailer_name and done_trailer_path and await aiofiles.os.path.exists(done_trailer_path):
        if await aiofiles.os.path.exists(trailer_file_path):
            await delete_file_async(trailer_file_path)
        await copy_file_async(done_trailer_path, trailer_file_path)
        LogBuffer.log().write(f"\n 🍀 Trailer done! (copy trailer)({get_used_time(start_time)}s)")
        return

    # 不下载时返回（选择不下载保留，但本地并不存在，此时返回）
    if DownloadableFile.TRAILER not in download_files:
        return

    # 下载预告片,检测链接有效性
    content_length = await check_url(trailer_url, length=True)
    if content_length:
        # 创建文件夹
        if trailer_name == 1 and not await aiofiles.os.path.exists(trailer_folder_path):
            await aiofiles.os.makedirs(trailer_folder_path)

        # 开始下载
        download_files = manager.config.download_files
        signal.show_traceback_log(f"🍔 {result.number} download trailer... {trailer_url}")
        trailer_file_path_temp = trailer_file_path
        if await aiofiles.os.path.exists(trailer_file_path):
            trailer_file_path_temp = trailer_file_path.with_suffix(".[DOWNLOAD].mp4")
        if await download_file_with_filepath(trailer_url, trailer_file_path_temp, trailer_folder_path):
            file_size = await aiofiles.os.path.getsize(trailer_file_path_temp)
            if file_size >= content_length or DownloadableFile.IGNORE_SIZE in download_files:
                LogBuffer.log().write(
                    f"\n 🍀 Trailer done! ({result.trailer_from} {file_size}/{content_length})({get_used_time(start_time)}s) "
                )
                signal.show_traceback_log(f"✅ {result.number} trailer done!")
                if trailer_file_path_temp != trailer_file_path:
                    await move_file_async(trailer_file_path_temp, trailer_file_path)
                    await delete_file_async(trailer_file_path_temp)
                done_trailer_path = Flags.file_done_dic.get(result.number, {}).get("trailer")
                if not done_trailer_path:
                    Flags.file_done_dic[result.number].update({"trailer": trailer_file_path})
                    if trailer_name == 0:  # 带文件名，已下载成功，删除掉那些不用的文件夹即可
                        if await aiofiles.os.path.exists(trailer_old_folder_path):
                            await to_thread(shutil.rmtree, trailer_old_folder_path, ignore_errors=True)
                        if trailer_new_folder_path != trailer_old_folder_path and await aiofiles.os.path.exists(
                            trailer_new_folder_path
                        ):
                            await to_thread(shutil.rmtree, trailer_new_folder_path, ignore_errors=True)
                return True
            else:
                LogBuffer.log().write(
                    f"\n 🟠 Trailer size is incorrect! delete it! ({result.trailer_from} {file_size}/{content_length}) "
                )

        # 删除下载失败的文件
        await delete_file_async(trailer_file_path_temp)
        LogBuffer.log().write(f"\n 🟠 Trailer download failed! ({trailer_url}) ")

    if await aiofiles.os.path.exists(trailer_file_path):  # 使用旧文件
        done_trailer_path = Flags.file_done_dic.get(result.number, {}).get("trailer")
        if not done_trailer_path:
            Flags.file_done_dic[result.number].update({"trailer": trailer_file_path})
            if trailer_name == 0:  # 带文件名，已下载成功，删除掉那些不用的文件夹即可
                if await aiofiles.os.path.exists(trailer_old_folder_path):
                    await to_thread(shutil.rmtree, trailer_old_folder_path, ignore_errors=True)
                if trailer_new_folder_path != trailer_old_folder_path and await aiofiles.os.path.exists(
                    trailer_new_folder_path
                ):
                    await to_thread(shutil.rmtree, trailer_new_folder_path, ignore_errors=True)
        LogBuffer.log().write("\n 🟠 Trailer download failed! 将继续使用之前的本地文件！")
        LogBuffer.log().write(f"\n 🍀 Trailer done! (old)({get_used_time(start_time)}s)")
        return True


async def _get_big_thumb(result: CrawlersResult, other: OtherInfo):
    """
    获取背景大图：
    1，官网图片
    2，Amazon 图片
    3，Google 搜图
    """
    start_time = time.time()
    if "thumb" not in manager.config.download_hd_pics:
        return
    number = result.number
    letters = result.letters
    number_lower_line = number.lower()
    thumb_width = 0

    # dahlia 图片默认较大，直接返回即可
    if result.thumb_from in ["dahlia"]:
        if result.thumb:
            LogBuffer.log().write(f"\n 🖼 HD Thumb found! ({result.thumb_from})({get_used_time(start_time)}s)")
        other.poster_big = True
        return result

    # mgstage 图片有的是大图，需要检测图片分辨率
    elif result.thumb_from in ["mgstage"]:
        if result.thumb:
            thumb_width, h = await get_imgsize(result.thumb)

    # 片商官网查询
    elif HDPicSource.OFFICIAL in manager.config.download_hd_pics:
        # km-produce.com 番号检查
        number_letter = letters.lower()
        kmp_key = ["vrkm", "mdtm", "mkmp", "savr", "bibivr", "scvr", "slvr", "averv", "kbvr", "cbikmv"]
        if number_letter in kmp_key:
            req_url = f"https://km-produce.com/img/title1/{number_lower_line}.jpg"
            real_url = await check_url(req_url)
            if real_url:
                result.thumb = real_url
                result.thumb_from = "km-produce"
                LogBuffer.log().write(f"\n 🖼 HD Thumb found! (km-produce)({get_used_time(start_time)}s)")
                return result

    # 使用google以图搜图
    pic_url = result.thumb
    if HDPicSource.GOOGLE in manager.config.download_hd_pics and pic_url and result.thumb_from != "theporndb":
        thumb_url, cover_size = await get_big_pic_by_google(pic_url)
        if thumb_url and cover_size[0] > thumb_width:
            other.thumb_size = cover_size
            pic_domain = re.findall(r"://([^/]+)", thumb_url)[0]
            result.thumb_from = f"Google({pic_domain})"
            result.thumb = thumb_url
            LogBuffer.log().write(f"\n 🖼 HD Thumb found! ({result.thumb_from})({get_used_time(start_time)}s)")

    return result


async def _get_big_poster(result: CrawlersResult, other: OtherInfo):
    start_time = time.time()

    # 未勾选下载高清图poster时，返回
    if "poster" not in manager.config.download_hd_pics:
        return

    # 如果有大图时，直接下载
    if other.poster_big and (await get_imgsize(result.poster))[1] > 600:
        result.image_download = True
        LogBuffer.log().write(f"\n 🖼 HD Poster found! ({result.poster_from})({get_used_time(start_time)}s)")
        return

    # 初始化数据
    number = result.number
    poster_url = result.poster
    hd_pic_url = ""
    poster_width = 0

    # 通过原标题去 amazon 查询
    if HDPicSource.AMAZON in manager.config.download_hd_pics and result.mosaic in [
        "有码",
        "有碼",
        "流出",
        "无码破解",
        "無碼破解",
        "里番",
        "裏番",
        "动漫",
        "動漫",
    ]:
        hd_pic_url = await get_big_pic_by_amazon(result, result.originaltitle_amazon, result.actor_amazon)
        if hd_pic_url:
            result.poster = hd_pic_url
            result.poster_from = "Amazon"
        if result.poster_from == "Amazon":
            result.image_download = True

    # 通过番号去 官网 查询获取稍微大一些的封面图，以便去 Google 搜索
    if not hd_pic_url and HDPicSource.OFFICIAL in manager.config.download_hd_pics and result.poster_from != "Amazon":
        letters = result.letters.upper()
        official_url = manager.computed.official_websites.get(letters)
        if official_url:
            url_search = official_url + "/search/list?keyword=" + number.replace("-", "")
            html_search, error = await manager.computed.async_client.get_text(url_search)
            if html_search is not None:
                poster_url_list = re.findall(r'img class="c-main-bg lazyload" data-src="([^"]+)"', html_search)
                if poster_url_list:
                    # 使用官网图作为封面去 google 搜索
                    poster_url = poster_url_list[0]
                    result.poster = poster_url
                    result.poster_from = official_url.split(".")[-2].replace("https://", "")
                    # vr作品或者官网图片高度大于500时，下载封面图开
                    if "VR" in number.upper() or (await get_imgsize(poster_url))[1] > 500:
                        result.image_download = True

    # 使用google以图搜图，放在最后是因为有时有错误，比如 kawd-943
    poster_url = result.poster
    if (
        not hd_pic_url
        and poster_url
        and HDPicSource.GOOGLE in manager.config.download_hd_pics
        and result.poster_from != "theporndb"
    ):
        hd_pic_url, poster_size = await get_big_pic_by_google(poster_url, poster=True)
        if hd_pic_url:
            if result.poster_from == "Amazon":
                poster_width, _ = await get_imgsize(poster_url)
            if poster_size[0] > poster_width:
                result.poster = hd_pic_url
                other.poster_size = poster_size
                pic_domain = re.findall(r"://([^/]+)", hd_pic_url)[0]
                result.poster_from = f"Google({pic_domain})"

    # 如果找到了高清链接，则替换
    if hd_pic_url:
        result.image_download = True
        LogBuffer.log().write(f"\n 🖼 HD Poster found! ({result.poster_from})({get_used_time(start_time)}s)")

    return result


async def thumb_download(
    result: CrawlersResult,
    other: OtherInfo,
    cd_part: str,
    folder_new_path: Path,
    thumb_final_path: Path,
) -> bool:
    start_time = time.time()
    poster_path = other.poster_path
    thumb_path = other.thumb_path
    fanart_path = other.fanart_path

    # 本地存在 thumb.jpg，且勾选保留旧文件时，不下载
    if thumb_path and DownloadableFile.THUMB in manager.config.keep_files:
        LogBuffer.log().write(f"\n 🍀 Thumb done! (old)({get_used_time(start_time)}s) ")
        return True

    # 如果thumb不下载，看fanart、poster要不要下载，都不下载则返回
    if DownloadableFile.THUMB not in manager.config.download_files:
        if (
            DownloadableFile.POSTER in manager.config.download_files
            and (DownloadableFile.POSTER not in manager.config.keep_files or not poster_path)
            or DownloadableFile.FANART in manager.config.download_files
            and (DownloadableFile.FANART not in manager.config.keep_files or not fanart_path)
        ):
            pass
        else:
            return True

    # 尝试复制其他分集。看分集有没有下载，如果下载完成则可以复制，否则就自行下载
    if cd_part:
        done_thumb_path = Flags.file_done_dic.get(result.number, {}).get("thumb")
        if (
            done_thumb_path
            and await aiofiles.os.path.exists(done_thumb_path)
            and split_path(done_thumb_path)[0] == split_path(thumb_final_path)[0]
        ):
            await copy_file_async(done_thumb_path, thumb_final_path)
            LogBuffer.log().write(f"\n 🍀 Thumb done! (copy cd-thumb)({get_used_time(start_time)}s) ")
            result.thumb_from = "copy cd-thumb"
            other.thumb_path = thumb_final_path
            return True

    # 获取高清背景图
    await _get_big_thumb(result, other)

    # 下载图片
    cover_url = result.thumb
    cover_from = result.thumb_from
    if cover_url:
        cover_list = result.thumb_list
        while (cover_from, cover_url) in cover_list:
            cover_list.remove((cover_from, cover_url))
        cover_list.insert(0, (cover_from, cover_url))

        thumb_final_path_temp = thumb_final_path
        if await aiofiles.os.path.exists(thumb_final_path):
            thumb_final_path_temp = thumb_final_path.with_suffix(".[DOWNLOAD].jpg")
        for each in cover_list:
            if not each[1]:
                continue
            cover_from, cover_url = each
            if not cover_url:
                LogBuffer.log().write(
                    f"\n 🟠 检测到 Thumb 图片失效! 跳过！({cover_from})({get_used_time(start_time)}s) " + each[1]
                )
                continue
            result.thumb_from = cover_from
            if await download_file_with_filepath(cover_url, thumb_final_path_temp, folder_new_path):
                cover_size = await check_pic_async(thumb_final_path_temp)
                if cover_size:
                    if (
                        not cover_from.startswith("Google")
                        or cover_size == other.thumb_size
                        or (
                            cover_size[0] >= 800
                            and abs(cover_size[0] / cover_size[1] - other.thumb_size[0] / other.thumb_size[1]) <= 0.1
                        )
                    ):
                        # 图片下载正常，替换旧的 thumb.jpg
                        if thumb_final_path_temp != thumb_final_path:
                            await move_file_async(thumb_final_path_temp, thumb_final_path)
                            await delete_file_async(thumb_final_path_temp)
                        if cd_part:
                            Flags.file_done_dic[result.number].update({"thumb": thumb_final_path})
                        other.thumb_marked = False  # 表示还没有走加水印流程
                        LogBuffer.log().write(f"\n 🍀 Thumb done! ({result.thumb_from})({get_used_time(start_time)}s) ")
                        other.thumb_path = thumb_final_path
                        return True
                    else:
                        await delete_file_async(thumb_final_path_temp)
                        LogBuffer.log().write(
                            f"\n 🟠 检测到 Thumb 分辨率不对{str(cover_size)}! 已删除 ({cover_from})({get_used_time(start_time)}s)"
                        )
                        continue
                LogBuffer.log().write(f"\n 🟠 Thumb download failed! {cover_from}: {cover_url} ")
    else:
        LogBuffer.log().write("\n 🟠 Thumb url is empty! ")

    # 下载失败，本地有图
    if thumb_path:
        LogBuffer.log().write("\n 🟠 Thumb download failed! 将继续使用之前的图片！")
        LogBuffer.log().write(f"\n 🍀 Thumb done! (old)({get_used_time(start_time)}s) ")
        return True
    else:
        if DownloadableFile.IGNORE_PIC_FAIL in manager.config.download_files:
            LogBuffer.log().write("\n 🟠 Thumb download failed! (你已勾选「图片下载失败时，不视为失败！」) ")
            LogBuffer.log().write(f"\n 🍀 Thumb done! (none)({get_used_time(start_time)}s)")
            return True
        else:
            LogBuffer.log().write(
                "\n 🔴 Thumb download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
            )
            LogBuffer.error().write(
                "Thumb download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
            )
            return False


async def poster_download(
    result: CrawlersResult,
    other: OtherInfo,
    cd_part: str,
    folder_new_path: Path,
    poster_final_path: Path,
) -> bool:
    start_time = time.time()
    download_files = manager.config.download_files
    keep_files = manager.config.keep_files
    poster_path = other.poster_path
    thumb_path = other.thumb_path
    fanart_path = other.fanart_path
    image_cut = ""

    # 不下载poster、不保留poster时，返回
    if DownloadableFile.POSTER not in download_files and DownloadableFile.POSTER not in keep_files:
        if poster_path:
            await delete_file_async(poster_path)
        return True

    # 本地有poster时，且勾选保留旧文件时，不下载
    if poster_path and DownloadableFile.POSTER in keep_files:
        LogBuffer.log().write(f"\n 🍀 Poster done! (old)({get_used_time(start_time)}s)")
        return True

    # 不下载时返回
    if DownloadableFile.POSTER not in download_files:
        return True

    # 尝试复制其他分集。看分集有没有下载，如果下载完成则可以复制，否则就自行下载
    if cd_part:
        done_poster_path = Flags.file_done_dic.get(result.number, {}).get("poster")
        if (
            done_poster_path
            and await aiofiles.os.path.exists(done_poster_path)
            and split_path(done_poster_path)[0] == split_path(poster_final_path)[0]
        ):
            await copy_file_async(done_poster_path, poster_final_path)
            result.poster_from = "copy cd-poster"
            other.poster_path = poster_final_path
            LogBuffer.log().write(f"\n 🍀 Poster done! (copy cd-poster)({get_used_time(start_time)}s)")
            return True

    # 勾选复制 thumb时：国产，复制thumb；无码，勾选不裁剪时，也复制thumb
    if thumb_path:
        mosaic = result.mosaic
        number = result.number
        copy_flag = False
        if number.startswith("FC2"):
            image_cut = "center"
            if DownloadableFile.IGNORE_FC2 in download_files:
                copy_flag = True
        elif mosaic == "国产" or mosaic == "國產":
            image_cut = "right"
            if DownloadableFile.IGNORE_GUOCHAN in download_files:
                copy_flag = True
        elif mosaic == "无码" or mosaic == "無碼" or mosaic == "無修正":
            image_cut = "center"
            if DownloadableFile.IGNORE_WUMA in download_files:
                copy_flag = True
        elif mosaic == "有码" or mosaic == "有碼":
            if DownloadableFile.IGNORE_YOUMA in download_files:
                copy_flag = True
        if copy_flag:
            await copy_file_async(thumb_path, poster_final_path)
            other.poster_marked = other.thumb_marked
            result.poster_from = "copy thumb"
            other.poster_path = poster_final_path
            LogBuffer.log().write(f"\n 🍀 Poster done! (copy thumb)({get_used_time(start_time)}s)")
            return True

    # 获取高清 poster
    await _get_big_poster(result, other)

    # 下载图片
    poster_url = result.poster
    poster_from = result.poster_from
    poster_final_path_temp = poster_final_path
    if await aiofiles.os.path.exists(poster_final_path):
        poster_final_path_temp = poster_final_path.with_suffix(".[DOWNLOAD].jpg")
    if result.image_download:
        start_time = time.time()
        if await download_file_with_filepath(poster_url, poster_final_path_temp, folder_new_path):
            poster_size = await check_pic_async(poster_final_path_temp)
            if poster_size:
                if (
                    not poster_from.startswith("Google")
                    or poster_size == other.poster_size
                    or "media-amazon.com" in poster_url
                ):
                    if poster_final_path_temp != poster_final_path:
                        await move_file_async(poster_final_path_temp, poster_final_path)
                        await delete_file_async(poster_final_path_temp)
                    if cd_part:
                        Flags.file_done_dic[result.number].update({"poster": poster_final_path})
                    other.poster_marked = False  # 下载的图，还没加水印
                    other.poster_path = poster_final_path
                    LogBuffer.log().write(f"\n 🍀 Poster done! ({poster_from})({get_used_time(start_time)}s)")
                    return True
                else:
                    await delete_file_async(poster_final_path_temp)
                    LogBuffer.log().write(f"\n 🟠 检测到 Poster 分辨率不对{str(poster_size)}! 已删除 ({poster_from})")

    # 判断之前有没有 poster 和 thumb
    if not poster_path and not thumb_path:
        other.poster_path = None
        if DownloadableFile.IGNORE_PIC_FAIL in download_files:
            LogBuffer.log().write("\n 🟠 Poster download failed! (你已勾选「图片下载失败时，不视为失败！」) ")
            LogBuffer.log().write(f"\n 🍀 Poster done! (none)({get_used_time(start_time)}s)")
            return True
        else:
            LogBuffer.log().write(
                "\n 🔴 Poster download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
            )
            LogBuffer.error().write(
                "Poster download failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
            )
            return False

    # 使用thumb裁剪
    poster_final_path_temp = poster_final_path.with_suffix(".[CUT].jpg")
    if fanart_path:
        thumb_path = fanart_path
    if thumb_path and await asyncio.to_thread(
        cut_thumb_to_poster, result, thumb_path, poster_final_path_temp, image_cut
    ):
        # 裁剪成功，替换旧图
        await move_file_async(poster_final_path_temp, poster_final_path)
        if cd_part:
            Flags.file_done_dic[result.number].update({"poster": poster_final_path})
        other.poster_path = poster_final_path
        other.poster_marked = False
        return True

    # 裁剪失败，本地有图
    if poster_path:
        LogBuffer.log().write("\n 🟠 Poster cut failed! 将继续使用之前的图片！")
        LogBuffer.log().write(f"\n 🍀 Poster done! (old)({get_used_time(start_time)}s) ")
        return True
    else:
        if DownloadableFile.IGNORE_PIC_FAIL in download_files:
            LogBuffer.log().write("\n 🟠 Poster cut failed! (你已勾选「图片下载失败时，不视为失败！」) ")
            LogBuffer.log().write(f"\n 🍀 Poster done! (none)({get_used_time(start_time)}s)")
            return True
        else:
            LogBuffer.log().write(
                "\n 🔴 Poster cut failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
            )
            LogBuffer.error().write("Poster failed！你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」")
            return False


async def fanart_download(
    number: str,
    other: OtherInfo,
    cd_part: str,
    fanart_final_path: Path,
) -> bool:
    """
    复制thumb为fanart
    """
    start_time = time.time()
    thumb_path = other.thumb_path
    fanart_path = other.fanart_path
    download_files = manager.config.download_files
    keep_files = manager.config.keep_files

    # 不保留不下载时删除返回
    if DownloadableFile.FANART not in keep_files and DownloadableFile.FANART not in download_files:
        if fanart_path and await aiofiles.os.path.exists(fanart_path):
            await delete_file_async(fanart_path)
        return True

    # 保留，并且本地存在 fanart.jpg，不下载返回
    if DownloadableFile.FANART in keep_files and fanart_path:
        LogBuffer.log().write(f"\n 🍀 Fanart done! (old)({get_used_time(start_time)}s)")
        return True

    # 不下载时，返回
    if DownloadableFile.FANART not in download_files:
        return True

    # 尝试复制其他分集。看分集有没有下载，如果下载完成则可以复制，否则就自行下载
    if cd_part:
        done_fanart_path = Flags.file_done_dic.get(number, {}).get("fanart")
        if (
            done_fanart_path
            and await aiofiles.os.path.exists(done_fanart_path)
            and done_fanart_path.parent == fanart_final_path.parent
        ):
            if fanart_path:
                await delete_file_async(fanart_path)
            await copy_file_async(done_fanart_path, fanart_final_path)
            other.fanart_path = fanart_final_path
            LogBuffer.log().write(f"\n 🍀 Fanart done! (copy cd-fanart)({get_used_time(start_time)}s)")
            return True

    # 复制thumb
    if thumb_path:
        if fanart_path:
            await delete_file_async(fanart_path)
        await copy_file_async(thumb_path, fanart_final_path)
        other.fanart_path = fanart_final_path
        other.fanart_marked = other.thumb_marked
        LogBuffer.log().write(f"\n 🍀 Fanart done! (copy thumb)({get_used_time(start_time)}s)")
        if cd_part:
            Flags.file_done_dic[number].update({"fanart": fanart_final_path})
        return True
    else:
        # 本地有 fanart 时，不下载
        if fanart_path:
            LogBuffer.log().write("\n 🟠 Fanart copy failed! 未找到 thumb 图片，将继续使用之前的图片！")
            LogBuffer.log().write(f"\n 🍀 Fanart done! (old)({get_used_time(start_time)}s)")
            return True

        else:
            if DownloadableFile.IGNORE_PIC_FAIL in download_files:
                LogBuffer.log().write("\n 🟠 Fanart failed! (你已勾选「图片下载失败时，不视为失败！」) ")
                LogBuffer.log().write(f"\n 🍀 Fanart done! (none)({get_used_time(start_time)}s)")
                return True
            else:
                LogBuffer.log().write(
                    "\n 🔴 Fanart failed! 你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」 "
                )
                LogBuffer.error().write(
                    "Fanart 下载失败！你可以到「设置」-「下载」，勾选「图片下载失败时，不视为失败！」"
                )
                return False


async def extrafanart_download(extrafanart: list[str], extrafanart_from: str, folder_new_path: Path) -> bool | None:
    start_time = time.time()
    download_files = manager.config.download_files
    keep_files = manager.config.keep_files
    extrafanart_list = extrafanart
    extrafanart_folder_path = folder_new_path / "extrafanart"

    # 不下载不保留时删除返回
    if DownloadableFile.EXTRAFANART not in download_files and DownloadableFile.EXTRAFANART not in keep_files:
        if await aiofiles.os.path.exists(extrafanart_folder_path):
            await to_thread(shutil.rmtree, extrafanart_folder_path, ignore_errors=True)
        return

    # 本地存在 extrafanart_folder，且勾选保留旧文件时，不下载
    if DownloadableFile.EXTRAFANART in keep_files and await aiofiles.os.path.exists(extrafanart_folder_path):
        LogBuffer.log().write(f"\n 🍀 Extrafanart done! (old)({get_used_time(start_time)}s) ")
        return True

    # 如果 extrafanart 不下载
    if DownloadableFile.EXTRAFANART not in download_files:
        return True

    # 检测链接有效性
    if extrafanart_list and await check_url(extrafanart_list[0]):
        extrafanart_folder_path_temp = extrafanart_folder_path
        if await aiofiles.os.path.exists(extrafanart_folder_path_temp):
            extrafanart_folder_path_temp = extrafanart_folder_path.with_name(
                extrafanart_folder_path.name + "[DOWNLOAD]"
            )
            if not await aiofiles.os.path.exists(extrafanart_folder_path_temp):
                await aiofiles.os.makedirs(extrafanart_folder_path_temp)
        else:
            await aiofiles.os.makedirs(extrafanart_folder_path_temp)

        extrafanart_count = 0
        extrafanart_count_succ = 0
        task_list = []
        for extrafanart_url in extrafanart_list:
            extrafanart_count += 1
            extrafanart_name = "fanart" + str(extrafanart_count) + ".jpg"
            extrafanart_file_path = extrafanart_folder_path_temp / extrafanart_name
            task_list.append((extrafanart_url, extrafanart_file_path, extrafanart_folder_path_temp, extrafanart_name))

        # 使用异步并发执行下载任务
        tasks = [download_extrafanart_task(task) for task in task_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if res is True:
                extrafanart_count_succ += 1
        if extrafanart_count_succ == extrafanart_count:
            if extrafanart_folder_path_temp != extrafanart_folder_path:
                await to_thread(shutil.rmtree, extrafanart_folder_path)
                await aiofiles.os.rename(extrafanart_folder_path_temp, extrafanart_folder_path)
            LogBuffer.log().write(
                f"\n 🍀 ExtraFanart done! ({extrafanart_from} {extrafanart_count_succ}/{extrafanart_count})({get_used_time(start_time)}s)"
            )
            return True
        else:
            LogBuffer.log().write(
                f"\n 🟠 ExtraFanart download failed! ({extrafanart_from} {extrafanart_count_succ}/{extrafanart_count})({get_used_time(start_time)}s)"
            )
            if extrafanart_folder_path_temp != extrafanart_folder_path:
                await to_thread(shutil.rmtree, extrafanart_folder_path_temp)
            else:
                LogBuffer.log().write(f"\n 🍀 ExtraFanart done! (incomplete)({get_used_time(start_time)}s)")
                return False
        LogBuffer.log().write("\n 🟠 ExtraFanart download failed! 将继续使用之前的本地文件！")
    if await aiofiles.os.path.exists(extrafanart_folder_path):  # 使用旧文件
        LogBuffer.log().write(f"\n 🍀 ExtraFanart done! (old)({get_used_time(start_time)}s)")
        return True
