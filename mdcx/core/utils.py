import asyncio
import os
import re
from pathlib import Path

import aiofiles.os

from ..base.number import deal_actor_more
from ..config.enums import FieldRule, Language, NfoInclude, NoEscape, TagInclude
from ..config.manager import manager
from ..config.resources import resources
from ..gen.field_enums import CrawlerResultFields
from ..manual import ManualConfig
from ..models.log_buffer import LogBuffer
from ..models.types import BaseCrawlerResult, CrawlersResult, FileInfo
from ..number import get_number_first_letter, get_number_letters
from ..signals import signal
from ..utils import get_new_release, get_used_time, split_path
from ..utils.video import get_video_metadata


def replace_word(json_data: BaseCrawlerResult):
    # 常见字段替换的字符
    for key, value in ManualConfig.ALL_REP_WORD.items():
        for each in ManualConfig.ALL_KEY_WORD:
            setattr(json_data, each, getattr(json_data, each).replace(key, value))

    # 简体时替换的字符
    key_word = []
    if manager.config.get_field_config(CrawlerResultFields.TITLE).language == Language.ZH_CN:
        key_word.append("title")
    if manager.config.get_field_config(CrawlerResultFields.OUTLINE).language == Language.ZH_CN:
        key_word.append("outline")

    for key, value in ManualConfig.CHINESE_REP_WORD.items():
        for each in key_word:
            setattr(json_data, each, getattr(json_data, each).replace(key, value))

    # 替换标题的上下集信息
    for field in (CrawlerResultFields.TITLE, CrawlerResultFields.ORIGINALTITLE):
        for each in ManualConfig.TITLE_REP:
            setattr(json_data, field, getattr(json_data, field).replace(each, "").strip(":， ").strip())


def replace_special_word(json_data: BaseCrawlerResult):
    # 常见字段替换的字符
    all_key_word = [
        "title",
        "originaltitle",
        "outline",
        "originalplot",
        "series",
        "director",
        "studio",
        "publisher",
        "tag",
    ]
    for key, value in ManualConfig.SPECIAL_WORD.items():
        for each in all_key_word:
            # json_data[each] = json_data[each].replace(key, value)
            setattr(json_data, each, getattr(json_data, each).replace(key, value))


def deal_some_field(json_data: CrawlersResult):
    fields_rule = manager.config.fields_rule
    title = json_data.title
    originaltitle = json_data.originaltitle
    number = json_data.number

    # 演员处理
    if json_data.actors:
        # 去除演员名中的括号
        temp_actor_list = []
        actors = json_data.actors.copy()
        json_data.actors = []
        for raw_name in actors:
            if not raw_name:
                continue
            cleaned = re.findall(r"[^\(\)\（\）]+", raw_name)
            temp_actor_list.extend(cleaned)
            if FieldRule.DEL_CHAR in fields_rule:
                json_data.actors.append(cleaned[0])
            else:
                json_data.actors.append(raw_name)

        # 去除 all_actors 中的括号
        all_actors = json_data.all_actors.copy()
        json_data.all_actors = []
        for raw_name in all_actors:
            if not raw_name:
                continue
            cleaned = re.findall(r"[^\(\)\（\）]+", raw_name)
            if FieldRule.DEL_CHAR in fields_rule:
                json_data.all_actors.append(cleaned[0])
            else:
                json_data.all_actors.append(raw_name)

        # 去除标题后的演员名
        if FieldRule.DEL_ACTOR in fields_rule:
            new_all_actor_name_list = []
            for each_actor in json_data.actor_amazon + temp_actor_list:
                # 获取演员映射表的所有演员别名进行替换
                actor_keyword_list: list[str] = resources.get_actor_data(each_actor).get("keyword", [])
                new_all_actor_name_list.extend(actor_keyword_list)
            for each_actor in set(new_all_actor_name_list):
                title = title.removesuffix(f" {each_actor}")
                originaltitle = originaltitle.removesuffix(f" {each_actor}")
        json_data.title = title.strip()
        json_data.originaltitle = originaltitle.strip()

    # 去除标题中的番号
    if number != title and title.startswith(number):
        title = title.replace(number, "").strip()
        json_data.title = title
    if number != originaltitle and originaltitle.startswith(number):
        originaltitle = originaltitle.replace(number, "").strip()
        json_data.originaltitle = originaltitle

    # 去除标题中的/
    json_data.title = json_data.title.replace("/", "#").strip(" -")
    json_data.originaltitle = json_data.originaltitle.replace("/", "#").strip(" -")

    # 去除素人番号前缀数字
    if FieldRule.DEL_NUM in fields_rule:
        temp_n = re.findall(r"\d{3,}([a-zA-Z]+-\d+)", number)
        if temp_n:
            json_data.number = temp_n[0]
            json_data.letters = get_number_letters(json_data.number)

    if number.endswith("Z"):
        json_data.number = json_data.number[:-1] + "z"
    return json_data


def show_movie_info(file_info: FileInfo, result: CrawlersResult):
    if not manager.config.show_data_log:  # 调试模式打开时显示详细日志
        return
    for key in ManualConfig.SHOW_KEY:  # 大部分来自 CrawlersResultDataClass, 少部分来自 FileInfo
        value = getattr(result, key, getattr(file_info, key, ""))
        if not value:
            continue
        if key == CrawlerResultFields.OUTLINE or key == CrawlerResultFields.ORIGINALPLOT and len(value) > 100:
            value = str(value)[:98] + "……（略）"
        elif key == "has_sub":
            value = "中文字幕"
        elif key == CrawlerResultFields.ACTORS and NfoInclude.ACTOR_ALL in manager.config.nfo_include_new:
            value = result.all_actor
        LogBuffer.log().write(f"\n     {key:<13}: {value}")


async def get_video_size(file_path: Path):
    """
    获取视频分辨率和编码格式

    Args:
        file_path (Path): 视频文件的完整路径

    Returns:
        definition,codec (tuple[str, str]): 视频分辨率, 编码格式
    """
    # 获取本地分辨率 同时获取视频编码格式
    definition = ""
    height = 0
    hd_get = manager.config.hd_get
    if await aiofiles.os.path.islink(file_path):
        if NoEscape.SYMLINK_DEFINITION in manager.config.no_escape:
            file_path = file_path.resolve()
        else:
            hd_get = "path"
    codec = ""
    if hd_get == "video":
        try:
            height, codec = await asyncio.to_thread(get_video_metadata, file_path)
        except Exception as e:
            signal.show_log_text(f" 🔴 无法获取视频分辨率! 文件地址: {file_path}  错误信息: {e}")
    elif hd_get == "path":
        file_path_temp = file_path.as_posix().upper()
        if "8K" in file_path_temp:
            height = 4000
        elif "4K" in file_path_temp or "UHD" in file_path_temp:
            height = 2000
        elif "1440P" in file_path_temp or "QHD" in file_path_temp:
            height = 1440
        elif "1080P" in file_path_temp or "FHD" in file_path_temp:
            height = 1080
        elif "960P" in file_path_temp:
            height = 960
        elif "720P" in file_path_temp or "HD" in file_path_temp:
            height = 720

    hd_name = manager.config.hd_name
    if not height:
        pass
    elif height >= 4000:
        definition = "8K" if hd_name == "height" else "UHD8"
    elif height >= 2000:
        definition = "4K" if hd_name == "height" else "UHD"
    elif height >= 1400:
        definition = "1440P" if hd_name == "height" else "QHD"
    elif height >= 1000:
        definition = "1080P" if hd_name == "height" else "FHD"
    elif height >= 900:
        definition = "960P" if hd_name == "height" else "HD"
    elif height >= 700:
        definition = "720P" if hd_name == "height" else "HD"
    elif height >= 500:
        definition = "540P" if hd_name == "height" else "qHD"
    elif height >= 400:
        definition = "480P"
    elif height >= 300:
        definition = "360P"
    elif height >= 100:
        definition = "144P"

    return definition, codec


def add_definition_tag(res: BaseCrawlerResult, definition, codec):
    remove_key = ["144P", "360P", "480P", "540P", "720P", "960P", "1080P", "1440P", "2160P", "4K", "8K"]
    tag = res.tag
    for each_key in remove_key:
        tag = tag.replace(each_key, "").replace(each_key.lower(), "")
    tag_list = re.split(r"[,，]", tag)
    new_tag_list = []
    [new_tag_list.append(i) for i in tag_list if i]
    if definition and TagInclude.DEFINITION in manager.config.nfo_tag_include:
        new_tag_list.insert(0, definition)
        if manager.config.hd_get == "video" and codec and codec not in new_tag_list:
            new_tag_list.insert(0, codec)  # 插入编码格式
    res.tag = ",".join(new_tag_list)


def show_result(res: CrawlersResult, start_time: float):
    LogBuffer.log().write(res.site_log)
    if manager.config.show_from_log and res.field_log:  # 字段来源信息
        LogBuffer.log().write("\n\n 📒 字段来源\n\n" + res.field_log.strip(" ").strip("\n"))
    LogBuffer.log().write(f"\n 🍀 Data done!({get_used_time(start_time)}s)")


def _normalize_template_value(value, *, blank_zero: bool = False) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "none":
        return ""
    if blank_zero and text in {"0", "0.0", "0.00"}:
        return ""
    return text


def _cleanup_empty_template_parts(rendered_name: str) -> str:
    empty_pairs = [r"\[\s*\]", r"【\s*】", r"\(\s*\)", r"（\s*）"]
    for pattern in empty_pairs:
        rendered_name = re.sub(rf"\s*{pattern}\s*", " ", rendered_name)
    rendered_name = re.sub(r"\s{2,}", " ", rendered_name)
    return rendered_name.strip()


def render_name_template(
    template: str,
    file_info: FileInfo,
    json_data: CrawlersResult,
    show_4k: bool,
    show_cnword: bool,
    show_moword: bool,
    should_escape_result: bool,
    blank_empty_fields: bool = False,
) -> tuple[str, str, str, str, str, str]:
    """
    将模板字符串替换成实际值

    :param template: 设置——命名——视频命名规则 下的三个模板字符串
    :param file_path: 当前文件的完整路径，用于替换filename字段
    :param should_escape_result: 作为文件名/文件夹名时需要去掉一些特殊字符，作为nfo的<title>时则不用
    """
    file_path = file_info.file_path
    folder_path, file_full_name = split_path(file_path)  # 当前文件的目录和文件名
    filename = os.path.splitext(file_full_name)[0]

    # 获取文件信息
    destroyed = file_info.destroyed
    leak = file_info.leak
    wuma = file_info.wuma
    youma = file_info.youma
    m_word = destroyed + leak + wuma + youma
    c_word = file_info.c_word
    definition = file_info.definition

    title = _normalize_template_value(json_data.title)
    originaltitle = _normalize_template_value(json_data.originaltitle)
    studio = _normalize_template_value(json_data.studio)
    publisher = _normalize_template_value(json_data.publisher)
    year = _normalize_template_value(json_data.year, blank_zero=True)
    outline = _normalize_template_value(json_data.outline)
    runtime = _normalize_template_value(json_data.runtime, blank_zero=True)
    director = _normalize_template_value(json_data.director)
    actor = _normalize_template_value(json_data.actor)
    release = _normalize_template_value(json_data.release)
    number = _normalize_template_value(json_data.number)
    series = _normalize_template_value(json_data.series)
    mosaic = _normalize_template_value(json_data.mosaic)
    letters = _normalize_template_value(json_data.letters)

    # 是否勾选文件名添加4k标识
    temp_4k = ""
    if show_4k:
        definition = file_info.definition
        if definition == "8K" or definition == "UHD8" or definition == "4K" or definition == "UHD":
            temp_definition = definition.replace("UHD8", "UHD")
            temp_4k = f"-{temp_definition}"
    # 判断是否勾选文件名添加字幕标识
    cnword = c_word
    if not show_cnword:
        c_word = ""
    # 判断是否勾选文件名添加版本标识
    moword = m_word
    if not show_moword:
        m_word = ""
    # 判断后缀字段顺序
    suffix_sort_list = manager.config.suffix_sort
    for each in suffix_sort_list:
        if each == "moword":
            number += m_word
        elif each == "cnword":
            number += c_word
        elif each == "definition":
            number += temp_4k
    # 生成number
    first_letter = get_number_first_letter(number) if number else ""
    # 处理异常情况
    score = _normalize_template_value(json_data.score, blank_zero=True)
    wanted = _normalize_template_value(json_data.wanted, blank_zero=True)
    if blank_empty_fields:
        if not actor:
            actor = manager.config.actor_no_name
        release = get_new_release(release, manager.config.release_rule) if release else ""
    else:
        if not series:
            series = "未知系列"
        if not actor:
            actor = manager.config.actor_no_name
        if not year:
            year = "0000"
        if not score:
            score = "0.0"
        release = get_new_release(release, manager.config.release_rule)
    # 获取演员
    first_actor = actor.split(",").pop(0) if actor else ""
    all_actor_raw = _normalize_template_value(json_data.all_actor)
    all_actor = deal_actor_more(all_actor_raw) if all_actor_raw else ""
    actor = deal_actor_more(actor) if actor else ""

    # 替换字段里的文件夹分隔符
    if should_escape_result:
        fields = [originaltitle, title, number, director, actor, release, series, studio, publisher, cnword, outline]
        for i in range(len(fields)):
            fields[i] = fields[i].replace("/", "-").replace("\\", "-").strip(". ")
        originaltitle, title, number, director, actor, release, series, studio, publisher, cnword, outline = fields

    # 更新4k
    if definition == "8K" or definition == "UHD8" or definition == "4K" or definition == "UHD":
        temp_4k = definition.replace("UHD8", "UHD")
    # 替换文件名
    repl_list = [
        ("4K", temp_4k.strip("-")),
        ("originaltitle", originaltitle),
        ("title", title),
        ("outline", outline),
        ("number", number),
        ("first_actor", first_actor),
        ("all_actor", all_actor),
        ("actor", actor),
        ("release", release),
        ("year", str(year)),
        ("runtime", str(runtime)),
        ("director", director),
        ("series", series),
        ("studio", studio),
        ("publisher", publisher),
        ("mosaic", mosaic),
        ("definition", definition.replace("UHD8", "UHD")),
        ("cnword", cnword),
        ("moword", moword),
        ("first_letter", first_letter),
        ("letters", letters),
        ("filename", filename),
        ("wanted", wanted),
        ("score", score),
    ]

    # 国产使用title作为number会出现重复，此处去除title，避免重复(需要注意titile繁体情况)
    if not number:
        number = title
    # 默认emby视频标题配置为 [number title]，国产重复时需去掉一个，去重需注意空格也应一起去掉，否则国产的nfo标题中会多一个空格
    # 读取nfo title信息会去掉前面的number和空格以保留title展示出来，同时number和标题一致时，去掉number的逻辑变成去掉整个标题导致读取失败
    if number == title and "number" in template and "title" in template:
        template = template.replace("originaltitle", "").replace("title", "").strip()

    rendered_name = template
    for each_key in repl_list:
        rendered_name = rendered_name.replace(each_key[0], each_key[1])
    if blank_empty_fields:
        rendered_name = _cleanup_empty_template_parts(rendered_name)
    return rendered_name, template, number, originaltitle, outline, title
