from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from io import BytesIO
from pathlib import Path

from app_updater import handle_app_update_args, read_completed_app_update, update_app_from_remote
from database import DB_PATH, connect, ensure_local_version_files, initialize
from db_updater import (
    DECO_ASSET_BLOB_PATH,
    DECO_ASSET_MANIFEST_PATH,
    PNG_SIGNATURE,
    update_deco_assets_from_remote,
)
from market_prices import MaterialPrice, ResistMarketPrices, fetch_resist_market_prices
from reporter import (
    build_revision_request,
    clear_feedback_history,
    fetch_revision_replies,
    mark_feedback_replies_seen,
    submit_revision_request,
    unread_feedback_reply_count,
)
from search import (
    expand_search_terms,
    compact_search_text,
    fold_search_text,
    get_attributes,
    initial_search_matches,
    is_initial_search,
    normalize_search_keyword,
    search_entries,
    search_deco_entries_by_ingredient,
    search_deco_entries_by_name,
    search_recipe_entries_by_ingredient,
    search_recipe_entries_by_name,
    search_resist_entries_by_ingredient,
    search_resist_entries_by_name,
)

try:
    from PIL import Image
except ImportError:
    Image = None


SCOPES = {
    "1": ("equipment", "무기 / 방어구 / 엠블럼"),
    "2": ("accessory", "장신구"),
    "3": ("gathering", "생활 채집"),
    "4": ("barter", "물물교환"),
    "5": ("recipe", "제작법"),
    "6": ("deco", "데코 제작법"),
    "7": ("resist", "마도저항"),
}

TYPE_LABELS = {
    "WeaponRune": "무기",
    "ArmorRune": "방어구",
    "EmblemRune": "엠블럼",
    "AccessoryRune": "장신구",
    "Item": "아이템",
    "Barter": "물물교환",
    "Recipe": "제작법",
    "Deco": "데코 제작법",
    "Resist": "마도저항",
}

TIER_LABELS = {
    "Legendary": "전설",
    "Epic": "에픽",
    "Elite": "엘리트",
    "Mythic": "신화",
    "Exchange": "교환",
    "Raid": "레이드",
}

RESET = "\033[0m"
LIGHT_GREEN = "\033[38;5;114m"
HIGHLIGHT = "\033[93m"
GRAY = "\033[90m"
TIER_STYLES = {
    "전설": "\033[91m",
    "신화": "\033[93m",
    "엘리트": "\033[38;5;135m",
    "에픽": "\033[38;5;199m",
}
ITEM_NAME_TIER_STYLES = {
    "일반": GRAY,
    "고급": LIGHT_GREEN,
    "레어": "\033[94m",
    **TIER_STYLES,
}
QUIT_COMMANDS = {"2", "/q", "/quit", "q", "quit", "exit"}
BACK_COMMANDS = {"1", "/back", "back"}
REVISION_REQUEST_COMMANDS = {"0"}
PREVIOUS_PAGE_COMMAND = "/이전"
NEXT_PAGE_COMMAND = "/다음"
USAGE_COLUMN_SIZE = 50
USAGE_COLUMN_COUNT = 2
USAGE_PAGE_SIZE = USAGE_COLUMN_SIZE * USAGE_COLUMN_COUNT
GOLDEN_MATERIAL_NAME = "황금 재료"
GOLDEN_MATERIAL_CARD_SCALE = 1.5
FISHING_CARD_NAME = "낚시"
DECO_SIXEL_WIDTH = 64
DECO_SIXEL_HEIGHT = 64
DECO_IMAGE_ROW_HEIGHT = 3
DECO_IMAGE_CELL_COLUMNS = 8
DECO_IMAGE_CELL_ROWS = 3
DECO_RECIPE_ROW_HEIGHT = 4
TERMINAL_BG_RGB = (12, 12, 12)
TRANSPARENT_ALPHA_THRESHOLD = 0
WT_RELAUNCH_ENV = "MABIDB_WT_RELAUNCHED"
PYINSTALLER_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"
DECO_IMAGE_CARDS_ENABLED = False
DECO_ASSET_MANIFEST_CACHE: dict[str, dict[str, int]] | None = None
URL_PATTERN = re.compile(r"https?://\S+")
DESCRIPTION_BREAK_PATTERN = re.compile(r"(?<!:)//")
ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")
RECIPE_QUANTITY_PATTERN = re.compile(r"^(?P<name>.*?)\s*[×xX]\s*(?P<quantity>\d+)\s*$")


def configure_console() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def char_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


def display_width(text: object) -> int:
    return sum(char_width(char) for char in strip_ansi(text))


def fit_cell(text: object, width: int) -> str:
    value = str(text)
    result = ""
    used = 0
    for char in value:
        width_for_char = char_width(char)
        if used + width_for_char > width:
            break
        result += char
        used += width_for_char
    return result + (" " * (width - used))


def center_cell(text: object, width: int) -> str:
    if "\033" in str(text):
        value = str(text)
        visible = strip_ansi(value)
        padding = max(0, width - display_width(visible))
        left = padding // 2
        right = padding - left
        return (" " * left) + value + (" " * right)

    value = fit_cell(text, width).rstrip()
    padding = max(0, width - display_width(value))
    left = padding // 2
    right = padding - left
    return (" " * left) + value + (" " * right)


def left_cell(text: object, width: int) -> str:
    if "\033" in str(text):
        value = str(text)
        padding = max(0, width - display_width(strip_ansi(value)))
        return value + (" " * padding)

    value = fit_cell(text, width).rstrip()
    padding = max(0, width - display_width(value))
    return value + (" " * padding)


def style_tier_text(text: str) -> str:
    styled = text
    for label, style in TIER_STYLES.items():
        styled = styled.replace(label, f"{style}{label}{RESET}")
    return styled


def strip_ansi(text: object) -> str:
    return ANSI_PATTERN.sub("", str(text))


def wrap_text(text: object, width: int) -> list[str]:
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in value.split("\n"):
        current = ""
        used = 0
        index = 0
        while index < len(raw_line):
            ansi_match = ANSI_PATTERN.match(raw_line, index)
            if ansi_match:
                current += ansi_match.group(0)
                index = ansi_match.end()
                continue

            char = raw_line[index]
            width_for_char = char_width(char)
            if used > 0 and used + width_for_char > width:
                lines.append(current)
                current = ""
                used = 0
                continue

            current += char
            used += width_for_char
            index += 1
        lines.append(current)
    return lines or [""]


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def terminal_width() -> int:
    columns = shutil.get_terminal_size((110, 30)).columns
    return max(72, min(int(columns * 0.9), 126))


def hline(left: str, middle: str, right: str, widths: list[int]) -> str:
    return left + middle.join("─" * width for width in widths) + right


def hline_with_joins(left: str, joins: list[str], right: str, widths: list[int]) -> str:
    if len(joins) != len(widths) - 1:
        raise ValueError("join count must be one less than width count")
    result = left + ("─" * widths[0])
    for join, width in zip(joins, widths[1:]):
        result += join + ("─" * width)
    return result + right


def print_full_box(lines: list[str]) -> None:
    width = terminal_width()
    content_width = width - 2
    print("┌" + ("─" * content_width) + "┐")
    for line in lines:
        for wrapped in wrap_text(line, content_width):
            print("│" + center_cell(wrapped, content_width) + "│")
    print("└" + ("─" * content_width) + "┘")


def print_header(title: str, scope_label: str | None = None) -> None:
    clear_screen()
    lines = [title]
    if scope_label:
        lines.append(f"선택 범위: {scope_label}")
    print_full_box(lines)
    print()


def choose_scope(update_result=None, app_update_result=None, deco_update_result=None, feedback_result=None) -> tuple[str, str, object | None]:
    startup_cards_pending = (
        update_result is not None
        or app_update_result is not None
        or deco_update_result is not None
        or feedback_result is not None
    )
    error_message = ""
    while True:
        print_header("mabiDB")
        if startup_cards_pending:
            print_startup_cards(app_update_result, update_result, deco_update_result, feedback_result)
            startup_cards_pending = False
        elif feedback_result is not None:
            print_startup_cards(None, None, None, feedback_result)
        print(f"{HIGHLIGHT}시즌2 신규 정보 업데이트 완료!. 오류 제보 환영합니다~ {RESET}")
        print()
        print("검색할 그룹을 선택하세요.\n\n초성 검색,영문검색을 지원합니다!\n  ex) ㅇㄷㅎㅂ > 아득한빛\n  ex) dkemr > 아득")
        print()
        print("  1. 무기 / 방어구 / 엠블럼 룬")
        print("  2. 장신구 룬")
        print("  3. 생활 채집")
        print("  4. 물물교환")
        print("  5. 제작법")
        print("  6. 데코 제작법")
        print("  7. 마도저항")
        print()
        if error_message:
            print(error_message)
            print()
            error_message = ""
        choice = input("번호 입력 > ").strip()
        if choice == "0":
            show_feedback_replies()
            feedback_result = fetch_revision_replies()
            continue
        if choice in SCOPES:
            scope, scope_label = SCOPES[choice]
            return scope, scope_label, feedback_result
        error_message = "0, 1, 2, 3, 4, 5, 6, 7 중 하나를 입력하세요."


def search_help_text(scope: str) -> str:
    if scope == "gathering":
        return "채집물 이름으로 검색 가능합니다. 예) 양털, 마나석 등\n초성검색도 가능합니다. ex) ㄷㄲㅇㅇㅌ > 두꺼운양털\n\n# 황금 재료는 '황금'을 검색해주세요"
    if scope == "barter":
        return "NPC명, 아이템명, 지역으로 검색 가능합니다. 예) 말콤, 상급 양털, 티르코네일\n초성검색도 가능합니다. ex) ㅁㅋ > 말콤"
    if scope == "recipe":
        return "아이템명, 재료명으로 검색 가능합니다. 예) 금은매운탕, 상급 양털\n초성검색도 가능합니다. ex) ㅊㄱ > 철괴\n\n# 가공시간은 6레벨 작업대 + 생활멤버십 (가공시간 -50%) 를 기준으로 작성되었습니다"
    if scope == "deco":
        return "데코명, 재료명으로 검색 가능합니다. 예) 협탁, 목재, 데코 제작 부품\n초성검색도 가능합니다. ex) ㅎㅌ > 협탁"
    if scope == "resist":
        return "잔영, 해연, 클래스명, 부위명 등으로 검색 가능합니다. 예) 잔영, 사제, 천옷, 반지 등등\n초성검색도 가능합니다. ex) ㅇㅈㅅㄷ > 엣지소드\n마도 저항 장비의 제작 성공률은 24레벨에 100%입니다. 각 제작대 6레벨이 필요합니다.\n\n# 거래소 시세 정보는 https://mabimobi.life/market 에서 제공하는 API를 사용하였습니다. "
    if scope == "accessory":
        return "이름, 클래스, 설명으로 검색 가능합니다. 예) 관통, 기사, 홀리스피어\n초성검색도 가능합니다. ex) ㅅㄹㅂㅋ > 수레바퀴"
    return "이름, 내용, 태그, 줄임말로 검색 가능합니다. 예) 쏟불, 무방비, 주피증\n초성검색도 가능합니다. ex) ㅃㅇㅈ > 뼈인장\n전체 룬 목록을 보려면 룬 종류를 입력하세요. ex) 방어구룬, 무기룬"


def is_quit_command(text: str) -> bool:
    return text.lower() in QUIT_COMMANDS


def is_back_command(text: str, *, allow_b: bool = False) -> bool:
    commands = BACK_COMMANDS | ({"b"} if allow_b else set())
    return text.lower() in commands


def is_revision_request_command(text: str) -> bool:
    return text.lower() in REVISION_REQUEST_COMMANDS


def attributes_to_dict(attributes) -> dict[str, str]:
    values = {}
    for attribute in attributes:
        key = attribute["key"]
        value = attribute["value"]
        if value:
            values[key] = value
    return values


def format_tier(tier: str) -> str:
    parts = [part.strip() for part in tier.split(",") if part.strip()]
    return ", ".join(TIER_LABELS.get(part, part) for part in parts)


def gathering_card_widths(width: int) -> list[int]:
    inner_width = width - 3
    label = display_width("채집 장소") + 2
    value = inner_width - label
    return [label, value]


def gathering_card_labels(row) -> tuple[str, str]:
    if row["name"] == GOLDEN_MATERIAL_NAME:
        return "채집 방법", "재료 목록"
    return "채집 장소", "채집 방법"


def is_fishing_gathering_card(row, attributes: dict[str, str]) -> bool:
    return row["name"] == FISHING_CARD_NAME or FISHING_CARD_NAME in attributes.get("태그", "")


def fishing_gathering_card_width(row, base_width: int, max_width: int) -> int:
    bullet = "· "
    description_width = 0
    for part in DESCRIPTION_BREAK_PATTERN.split(str(row["description"])):
        part = part.strip()
        if part:
            description_width = max(description_width, display_width(bullet + part))
    required_width = description_width + 6 if description_width else base_width
    return min(max_width, max(base_width, required_width))


def pad_line(line: str, width: int) -> str:
    return line + (" " * max(0, width - display_width(strip_ansi(line))))


def print_card_grid(cards: list[list[str]], card_width: int, *, use_two_columns: bool) -> None:
    gap = "   "
    step = 2 if use_two_columns else 1
    for index in range(0, len(cards), step):
        left = cards[index]
        right = cards[index + 1] if use_two_columns and index + 1 < len(cards) else None
        if right is None:
            for line in left:
                print(line)
        else:
            row_count = max(len(left), len(right))
            blank = " " * card_width
            for line_index in range(row_count):
                left_line = left[line_index] if line_index < len(left) else blank
                right_line = right[line_index] if line_index < len(right) else blank
                print(pad_line(left_line, card_width) + gap + right_line)

        if index + step < len(cards):
            print()
            print()


def render_wrapped_row(values: list[str], widths: list[int], aligns: list[str]) -> list[str]:
    wrapped_values = [wrap_text(value, max(1, width - 2)) for value, width in zip(values, widths)]
    row_count = max(len(lines) for lines in wrapped_values)
    rendered = []
    for row_index in range(row_count):
        cells = []
        for lines, width, align in zip(wrapped_values, widths, aligns):
            line = lines[row_index] if row_index < len(lines) else ""
            cells.append(left_cell(line, width) if align == "left" else center_cell(line, width))
        rendered.append("│" + "│".join(cells) + "│")
    return rendered


def blank_wrapped_row(widths: list[int]) -> str:
    return "│" + "│".join(" " * width for width in widths) + "│"


def render_wrapped_row_fixed(
    values: list[str],
    widths: list[int],
    aligns: list[str],
    height: int,
) -> list[str]:
    lines = render_wrapped_row(values, widths, aligns)
    padding = max(0, height - len(lines))
    top_padding = padding // 2
    bottom_padding = padding - top_padding
    return (
        [blank_wrapped_row(widths) for _ in range(top_padding)]
        + lines
        + [blank_wrapped_row(widths) for _ in range(bottom_padding)]
    )


def render_labeled_lines_row_fixed(
    label: str,
    value_lines: list[str],
    widths: list[int],
    *,
    height: int,
) -> list[str]:
    lines = value_lines or [""]
    label_index = min(height, len(lines)) // 2
    rendered = []
    for index, line in enumerate(lines):
        label_cell = center_cell(label if index == label_index else "", widths[0])
        value_cell = left_cell(line, widths[1])
        rendered.append("│" + label_cell + "│" + value_cell + "│")
    padding = max(0, height - len(rendered))
    top_padding = padding // 2
    bottom_padding = padding - top_padding
    return (
        [blank_wrapped_row(widths) for _ in range(top_padding)]
        + rendered
        + [blank_wrapped_row(widths) for _ in range(bottom_padding)]
    )


def method_wrapped_lines(method: str, width: int) -> list[str]:
    parts = [part.strip() for part in method.split("//") if part.strip()]
    if not parts:
        return [""]

    lines = []
    bullet = "· "
    indent = "  "
    for part in parts:
        wrapped = wrap_text(part, max(1, width - display_width(bullet)))
        for index, line in enumerate(wrapped):
            lines.append((bullet if index == 0 else indent) + line)
    return lines


def render_full_width_lines(lines: list[str], content_width: int, *, align: str = "center") -> list[str]:
    rendered = []
    for line in lines:
        if URL_PATTERN.search(line):
            rendered.append("│" + line + "│")
            continue
        for wrapped in wrap_text(line, max(1, content_width - 4)):
            cell = left_cell(wrapped, content_width) if align == "left" else center_cell(wrapped, content_width)
            rendered.append("│" + cell + "│")
    return rendered


def indented_wrapped_lines(text: object, width: int, indent: str = "  ") -> list[str]:
    value = str(text)
    if not value:
        return [""]
    wrapped = []
    bullet = "· "
    for part in DESCRIPTION_BREAK_PATTERN.split(value):
        part = part.strip()
        if URL_PATTERN.search(part):
            wrapped.append(bullet + part)
            continue
        for index, line in enumerate(wrap_text(part, max(1, width - display_width(bullet)))):
            wrapped.append((bullet if index == 0 else indent) + line)
    return wrapped


def render_full_width_lines_fixed(
    lines: list[str],
    content_width: int,
    *,
    align: str = "center",
    height: int,
) -> list[str]:
    rendered = render_full_width_lines(lines, content_width, align=align)
    while len(rendered) < height:
        rendered.append("│" + (" " * content_width) + "│")
    return rendered


def gathering_card_section_heights(row, attributes: dict[str, str], width: int) -> dict[str, int]:
    widths = gathering_card_widths(width)
    content_width = width - 2
    method_lines = method_wrapped_lines(attributes.get("방법", ""), widths[1])
    description_lines = indented_wrapped_lines(row["description"], content_width - 4)
    location_label, method_label = gathering_card_labels(row)

    return {
        "name": len(render_wrapped_row(["이름", row["name"]], widths, ["center", "center"])),
        "tag": len(render_wrapped_row(["분류", attributes.get("태그", "")], widths, ["center", "center"])),
        "location": len(render_wrapped_row([location_label, attributes.get("위치", "")], widths, ["center", "center"])),
        "method": len(render_labeled_lines_row_fixed(method_label, method_lines, widths, height=1)),
        "description": len(render_full_width_lines(description_lines, content_width, align="left")),
    }


def max_gathering_card_heights(entries: list[tuple[object, dict[str, str]]], width: int) -> dict[str, int]:
    heights = {"name": 1, "tag": 1, "location": 1, "method": 1, "description": 1}
    for row, attributes in entries:
        current = gathering_card_section_heights(row, attributes, width)
        for key, value in current.items():
            heights[key] = max(heights[key], value)
    return heights


def render_gathering_result_card(
    row,
    attributes: dict[str, str],
    width: int,
    section_heights: dict[str, int] | None = None,
) -> list[str]:
    widths = gathering_card_widths(width)
    content_width = width - 2
    method_lines = method_wrapped_lines(attributes.get("방법", ""), widths[1])
    description_lines = indented_wrapped_lines(row["description"], content_width - 4)
    heights = section_heights or gathering_card_section_heights(row, attributes, width)
    colored_name = f"{LIGHT_GREEN}{row['name']}{RESET}"
    location_label, method_label = gathering_card_labels(row)

    lines = [hline("┌", "┬", "┐", widths)]
    lines.extend(render_wrapped_row_fixed(["이름", colored_name], widths, ["center", "center"], heights["name"]))
    lines.append(hline("├", "┼", "┤", widths))
    lines.extend(render_wrapped_row_fixed(["분류", attributes.get("태그", "")], widths, ["center", "center"], heights["tag"]))
    lines.append(hline("├", "┼", "┤", widths))
    lines.extend(render_wrapped_row_fixed([location_label, attributes.get("위치", "")], widths, ["center", "center"], heights["location"]))
    lines.append(hline("├", "┼", "┤", widths))
    lines.extend(render_labeled_lines_row_fixed(method_label, method_lines, widths, height=heights["method"]))
    lines.append(hline("├", "┴", "┤", widths))
    lines.extend(
        render_full_width_lines_fixed(
            description_lines,
            content_width,
            align="left",
            height=heights["description"],
        )
    )
    lines.append("└" + ("─" * content_width) + "┘")
    return lines


def print_gathering_result_cards(rows, conn) -> None:
    width = terminal_width()
    gap = "   "
    use_two_columns = width >= 96
    card_width = (width - display_width(gap)) // 2 if use_two_columns else min(64, width)
    entries = [
        (row, attributes_to_dict(get_attributes(conn, row["id"])))
        for row in rows
    ]
    normal_entries = [
        (row, attributes)
        for row, attributes in entries
        if row["name"] != GOLDEN_MATERIAL_NAME and not is_fishing_gathering_card(row, attributes)
    ]
    section_heights = (
        max_gathering_card_heights(normal_entries, card_width)
        if normal_entries
        else None
    )

    printed_group = False

    def print_group(cards: list[list[str]], width: int, *, two_columns: bool) -> None:
        nonlocal printed_group
        if not cards:
            return
        if printed_group:
            print()
            print()
        print_card_grid(cards, width, use_two_columns=two_columns)
        printed_group = True

    pending_cards: list[list[str]] = []
    for row, attributes in entries:
        if row["name"] == GOLDEN_MATERIAL_NAME:
            print_group(pending_cards, card_width, two_columns=use_two_columns)
            pending_cards = []
            wide_card_width = min(width, round(card_width * GOLDEN_MATERIAL_CARD_SCALE))
            print_group(
                [render_gathering_result_card(row, attributes, wide_card_width)],
                wide_card_width,
                two_columns=False,
            )
            continue

        if is_fishing_gathering_card(row, attributes):
            print_group(pending_cards, card_width, two_columns=use_two_columns)
            pending_cards = []
            wide_card_width = fishing_gathering_card_width(row, card_width, width)
            print_group(
                [render_gathering_result_card(row, attributes, wide_card_width)],
                wide_card_width,
                two_columns=False,
            )
            continue

        pending_cards.append(render_gathering_result_card(row, attributes, card_width, section_heights))

    print_group(pending_cards, card_width, two_columns=use_two_columns)

def padded_display_width(value: str, minimum: int) -> int:
    return max(display_width(value) + 2, minimum)


def barter_table_base_widths(entries: list[tuple[object, dict[str, str]]]) -> list[int]:
    _, first_attributes = entries[0]
    arrow_width = 3
    give_width = max(
        [padded_display_width("주는 아이템", display_width("주는 아이템") + 2)]
        + [padded_display_width(attributes.get("요구 아이템", ""), 4) for row, attributes in entries]
    )
    get_width = max(
        [padded_display_width("받는 아이템", display_width("받는 아이템") + 2)]
        + [padded_display_width(attributes.get("획득 아이템", ""), 4) for row, attributes in entries]
    )
    frequency_width = max(
        [
            padded_display_width("교환횟수", display_width("교환횟수") + 2),
            padded_display_width(barter_location_text(first_attributes), 10),
        ]
        + [padded_display_width(attributes.get("횟수", ""), 8) for row, attributes in entries]
    )
    return [give_width, arrow_width, get_width, frequency_width]


def barter_table_widths_for_entries(
    entries: list[tuple[object, dict[str, str]]],
    content_width: int,
) -> list[int]:
    give_width, arrow_width, get_width, frequency_width = barter_table_base_widths(entries)
    base_content_width = give_width + arrow_width + get_width + frequency_width + 3
    if base_content_width <= content_width:
        extra = content_width - base_content_width
        give_width += extra // 2
        get_width += extra - (extra // 2)
    else:
        frequency_width = min(frequency_width, max(display_width("교환횟수") + 2, min(18, content_width // 5)))
        item_space = max(8, content_width - arrow_width - frequency_width - 3)
        give_width = item_space // 2
        get_width = item_space - give_width

    return [give_width, arrow_width, get_width, frequency_width]


def barter_header_widths_from_table(table_widths: list[int]) -> list[int]:
    left = table_widths[0] + table_widths[1] + table_widths[2] + 2
    return [left, table_widths[3]]


def column_break_positions(widths: list[int]) -> list[int]:
    positions = []
    position = 0
    for width in widths[:-1]:
        position += width
        positions.append(position)
        position += 1
    return positions


def transition_hline(content_width: int, upper_widths: list[int], lower_widths: list[int]) -> str:
    chars = ["─"] * content_width
    for position in column_break_positions(upper_widths):
        chars[position] = "┴"
    for position in column_break_positions(lower_widths):
        chars[position] = "┼" if chars[position] != "─" else "┬"
    return "├" + "".join(chars) + "┤"


def barter_card_width_for_entries(
    entries: list[tuple[object, dict[str, str]]],
    max_width: int,
) -> int:
    _, first_attributes = entries[0]
    table_widths = barter_table_base_widths(entries)
    table_content_width = sum(table_widths) + len(table_widths) - 1
    header_content_width = (
        padded_display_width(barter_header_left_text(first_attributes, len(entries)), 10)
        + padded_display_width(barter_location_text(first_attributes), 10)
        + 1
    )
    return min(max_width, max(table_content_width, header_content_width) + 2)


def barter_location_text(attributes: dict[str, str]) -> str:
    location = attributes.get("지역", "")
    detail_location = attributes.get("위치", "")
    if location and detail_location:
        return f"{location} · {detail_location}"
    return location or detail_location


def barter_header_left_text(attributes: dict[str, str], count: int) -> str:
    return attributes.get("NPC", "")


def highlighted_barter_header_left_text(attributes: dict[str, str], count: int, matches) -> str:
    return highlight_if_match(attributes.get("NPC", ""), matches)


def barter_group_key(attributes: dict[str, str]) -> tuple[str, str, str]:
    return (
        attributes.get("NPC", ""),
        attributes.get("지역", ""),
        attributes.get("위치", ""),
    )


def all_barter_entries(conn) -> list[tuple[object, dict[str, str]]]:
    rows = conn.execute(
        """
        SELECT
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            0 AS score,
            '' AS class_name,
            NULL AS skill_slot,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
        FROM entries e
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        WHERE e.source = 'db.xlsx#Barter'
        GROUP BY e.id, e.type, e.name, e.summary, e.description
        ORDER BY e.id ASC
        """
    ).fetchall()
    return [(row, attributes_to_dict(get_attributes(conn, row["id"]))) for row in rows]


def render_full_width_lines_centered_fixed(
    lines: list[str],
    content_width: int,
    *,
    align: str = "center",
    height: int,
) -> list[str]:
    rendered = render_full_width_lines(lines, content_width, align=align)
    padding = max(0, height - len(rendered))
    top_padding = padding // 2
    bottom_padding = padding - top_padding
    blank = "│" + (" " * content_width) + "│"
    return ([blank] * top_padding) + rendered + ([blank] * bottom_padding)


def barter_matcher(conn, keyword: str):
    initial_keyword = normalize_search_keyword(keyword)
    if is_initial_search(initial_keyword):
        return lambda value: bool(value) and initial_search_matches(value, initial_keyword)

    terms = expand_search_terms(conn, keyword)
    folded_terms = [fold_search_text(term) for term in terms]
    compact_terms = [compact_search_text(term) for term in terms]

    def matches(value: str) -> bool:
        if not value:
            return False
        folded_value = fold_search_text(value)
        compact_value = compact_search_text(value)
        return any(term in folded_value for term in folded_terms) or any(
            compact_term and compact_term in compact_value
            for compact_term in compact_terms
        )

    return matches


def highlight_if_match(value: str, matches) -> str:
    if value and matches(value):
        return f"{HIGHLIGHT}{value}{RESET}"
    return value


def barter_card_section_heights(
    entries: list[tuple[object, dict[str, str]]],
    width: int,
) -> dict[str, int]:
    content_width = width - 2
    table_widths = barter_table_widths_for_entries(entries, content_width)
    header_widths = barter_header_widths_from_table(table_widths)
    _, first_attributes = entries[0]
    header_height = len(
        render_wrapped_row(
            [
                barter_header_left_text(first_attributes, len(entries)),
                barter_location_text(first_attributes),
            ],
            header_widths,
            ["left", "left"],
        )
    )
    item_heights = []
    for row, attributes in entries:
        item_heights.append(
            len(
                render_wrapped_row(
                    [
                        attributes.get("요구 아이템", ""),
                        "→",
                        attributes.get("획득 아이템", ""),
                        attributes.get("횟수", ""),
                    ],
                    table_widths,
                    ["center", "center", "center", "center"],
                )
            )
        )

    return {
        "header": max(1, header_height),
        "items": item_heights,
    }


def render_barter_result_card(
    entries: list[tuple[object, dict[str, str]]],
    width: int,
    matches,
) -> list[str]:
    content_width = width - 2
    table_widths = barter_table_widths_for_entries(entries, content_width)
    header_widths = barter_header_widths_from_table(table_widths)
    heights = barter_card_section_heights(entries, width)
    _, first_attributes = entries[0]

    lines = [hline("┌", "┬", "┐", header_widths)]
    lines.extend(
        render_wrapped_row_fixed(
            [
                highlighted_barter_header_left_text(first_attributes, len(entries), matches),
                highlight_if_match(barter_location_text(first_attributes), matches),
            ],
            header_widths,
            ["center", "center"],
            height=heights["header"],
        )
    )
    lines.append(transition_hline(content_width, header_widths, table_widths))
    lines.extend(render_wrapped_row_fixed(["주는 아이템", "→", "받는 아이템", "교환횟수"], table_widths, ["center", "center", "center", "center"], 1))
    lines.append(hline("├", "┼", "┤", table_widths))
    for index, (row, attributes) in enumerate(entries):
        lines.extend(
            render_wrapped_row_fixed(
                [
                    highlight_if_match(attributes.get("요구 아이템", ""), matches),
                    "→",
                    highlight_if_match(attributes.get("획득 아이템", ""), matches),
                    highlight_if_match(attributes.get("횟수", ""), matches),
                ],
                table_widths,
                ["center", "center", "center", "center"],
                heights["items"][index],
            )
        )
        if index == len(entries) - 1:
            lines.append(hline("└", "┴", "┘", table_widths))
    return lines


def print_barter_result_cards(rows, conn, keyword: str) -> None:
    width = terminal_width()
    matched_entries = [(row, attributes_to_dict(get_attributes(conn, row["id"]))) for row in rows]
    matched_keys = list(dict.fromkeys(barter_group_key(attributes) for row, attributes in matched_entries))
    all_entries = all_barter_entries(conn)
    matches = barter_matcher(conn, keyword)
    groups = [
        [
            (row, attributes)
            for row, attributes in all_entries
            if barter_group_key(attributes) == key
        ]
        for key in matched_keys
    ]
    card_width = max(barter_card_width_for_entries(group, width) for group in groups)
    gap = "   "
    use_two_columns = width >= (card_width * 2 + display_width(gap))
    cards = [
        render_barter_result_card(group, card_width, matches)
        for group in groups
    ]
    print_card_grid(cards, card_width, use_two_columns=use_two_columns)


def rune_card_widths(width: int) -> list[int]:
    inner_width = width - 3
    label = display_width("변경 스킬") + 2
    value = inner_width - label
    return [label, value]


def rune_card_rows(row, attributes: dict[str, str]) -> list[tuple[str, str, str, str]]:
    tier = attributes.get("등급", "")
    rows = [
        ("name", "이름", f"{LIGHT_GREEN}{row['name']}{RESET}", "center"),
        ("type", "룬 종류", TYPE_LABELS.get(row["type"], row["type"]), "center"),
        ("tier", "등급", style_tier_text(format_tier(tier)) if tier else "", "center"),
    ]

    if attributes.get("직업"):
        rows.append(("class", "직업", attributes["직업"], "center"))
    if attributes.get("스킬 슬롯"):
        rows.append(("skill_slot", "변경 스킬", attributes["스킬 슬롯"], "center"))

    description = row["description"] or row["summary"]
    if description:
        rows.append(("description", "설명", description, "left"))
    if attributes.get("태그"):
        rows.append(("tags", "태그", attributes["태그"], "left"))
    return rows


def rune_value_lines(key: str, value: str, width: int) -> list[str]:
    content_width = max(1, width - 2)
    if key in {"description", "tags"}:
        return [" " + line.lstrip() for line in wrap_text(value, max(1, content_width - 1))]
    return wrap_text(value, content_width)


def render_labeled_value_row_fixed(
    label: str,
    value_lines: list[str],
    widths: list[int],
    value_align: str,
    height: int,
) -> list[str]:
    padding = max(0, height - len(value_lines))
    top_padding = padding // 2
    bottom_padding = padding - top_padding
    padded_value_lines = ([""] * top_padding) + value_lines + ([""] * bottom_padding)
    label_index = (len(padded_value_lines) - 1) // 2

    rendered = []
    for index, line in enumerate(padded_value_lines):
        label_cell = center_cell(label if index == label_index else "", widths[0])
        value_cell = left_cell(line, widths[1]) if value_align == "left" else center_cell(line, widths[1])
        rendered.append("│" + label_cell + "│" + value_cell + "│")
    return rendered


def rune_card_section_heights(row, attributes: dict[str, str], width: int) -> dict[str, int]:
    widths = rune_card_widths(width)
    return {
        key: max(1, len(rune_value_lines(key, value, widths[1])))
        for key, label, value, value_align in rune_card_rows(row, attributes)
    }


def max_rune_card_heights(entries: list[tuple[object, dict[str, str]]], width: int) -> dict[str, int]:
    heights = {}
    for row, attributes in entries:
        current = rune_card_section_heights(row, attributes, width)
        for key, value in current.items():
            heights[key] = max(heights.get(key, 1), value)
    return heights


def render_result_card(
    row,
    attributes: dict[str, str],
    width: int,
    section_heights: dict[str, int] | None = None,
) -> list[str]:
    widths = rune_card_widths(width)
    rows = rune_card_rows(row, attributes)
    heights = section_heights or rune_card_section_heights(row, attributes, width)
    lines = [hline("┌", "┬", "┐", widths)]
    for index, (key, label, value, value_align) in enumerate(rows):
        lines.extend(
            render_labeled_value_row_fixed(
                label,
                rune_value_lines(key, value, widths[1]),
                widths,
                value_align,
                heights[key],
            )
        )
        if index == len(rows) - 1:
            lines.append(hline("└", "┴", "┘", widths))
        else:
            lines.append(hline("├", "┼", "┤", widths))
    return lines


def print_result_cards(rows, conn) -> None:
    width = terminal_width()
    gap = "   "
    use_two_columns = width >= 96
    card_width = (width - display_width(gap)) // 2 if use_two_columns else min(64, width)
    entries = [
        (row, attributes_to_dict(get_attributes(conn, row["id"])))
        for row in rows
    ]
    section_heights = max_rune_card_heights(entries, card_width)
    cards = [
        render_result_card(row, attributes, card_width, section_heights)
        for row, attributes in entries
    ]
    print_card_grid(cards, card_width, use_two_columns=use_two_columns)


def recipe_card_widths(width: int) -> list[int]:
    inner_width = width - 3
    label = display_width("제작 재료") + 2
    value = inner_width - label
    return [label, value]


def detect_deco_image_card_support() -> bool:
    return bool(os.environ.get("WT_SESSION")) and Image is not None


def should_relaunch_in_windows_terminal() -> bool:
    return (
        os.name == "nt"
        and bool(getattr(sys, "frozen", False))
        and not os.environ.get("WT_SESSION")
        and not os.environ.get(WT_RELAUNCH_ENV)
        and shutil.which("wt") is not None
    )


def detach_current_console() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def relaunch_in_windows_terminal_if_needed() -> bool:
    if not should_relaunch_in_windows_terminal():
        return False

    wt_path = shutil.which("wt")
    if wt_path is None:
        return False
    exe_path = Path(sys.executable).resolve()
    env = os.environ.copy()
    env[WT_RELAUNCH_ENV] = "1"
    env[PYINSTALLER_RESET_ENV] = "1"

    try:
        subprocess.Popen(
            [wt_path, "-w", "-1", "new-tab", "--title", "mabiDB", str(exe_path)],
            cwd=str(exe_path.parent),
            env=env,
            close_fds=True,
        )
        detach_current_console()
        return True
    except OSError:
        return False


def load_deco_asset_manifest() -> dict[str, dict[str, int]] | None:
    global DECO_ASSET_MANIFEST_CACHE

    if DECO_ASSET_MANIFEST_CACHE is not None:
        return DECO_ASSET_MANIFEST_CACHE
    if not DECO_ASSET_BLOB_PATH.exists() or not DECO_ASSET_MANIFEST_PATH.exists():
        return None

    try:
        manifest = json.loads(DECO_ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None

    DECO_ASSET_MANIFEST_CACHE = manifest
    return DECO_ASSET_MANIFEST_CACHE


def deco_asset_key(row, attributes: dict[str, str]) -> str | None:
    deco_type = attributes.get("종류")
    name = row["name"]
    if not deco_type or not name:
        return None
    return f"{deco_type}/{name}.png"


def deco_asset_bytes(row, attributes: dict[str, str]) -> bytes | None:
    manifest = load_deco_asset_manifest()
    key = deco_asset_key(row, attributes)
    if manifest is None or key is None:
        return None

    entry = manifest.get(key)
    if not isinstance(entry, dict):
        return None

    offset = entry.get("offset")
    size = entry.get("size")
    if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0:
        return None

    try:
        with DECO_ASSET_BLOB_PATH.open("rb") as blob_file:
            blob_file.seek(offset)
            data = blob_file.read(size)
    except OSError:
        return None

    if len(data) != size or not data.startswith(PNG_SIGNATURE):
        return None
    return data


def cropped_deco_icon(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        return image

    left, top, right, bottom = box
    pad_x = max(2, round((right - left) * 0.08))
    pad_y = max(2, round((bottom - top) * 0.08))
    return image.crop(
        (
            max(0, left - pad_x),
            max(0, top - pad_y),
            min(image.width, right + pad_x),
            min(image.height, bottom + pad_y),
        )
    )


def prepare_deco_icon(data: bytes, *, width: int = DECO_SIXEL_WIDTH, height: int = DECO_SIXEL_HEIGHT) -> Image.Image:
    if Image is None:
        raise RuntimeError("Pillow is not available")

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image = cropped_deco_icon(Image.open(BytesIO(data)).convert("RGBA"))
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    x = (width - image.width) // 2
    y = (height - image.height) // 2
    canvas.alpha_composite(image, (x, y))
    return canvas


def quantize_deco_icon_for_sixel(
    image: Image.Image,
    *,
    colors: int = 64,
) -> tuple[Image.Image, list[tuple[int, int, int]], Image.Image]:
    palette_method = Image.Palette.ADAPTIVE if hasattr(Image, "Palette") else Image.ADAPTIVE
    background = Image.new("RGBA", image.size, (*TERMINAL_BG_RGB, 255))
    blended = Image.alpha_composite(background, image).convert("RGB")
    visible = image.getchannel("A").point(lambda alpha: 255 if alpha > TRANSPARENT_ALPHA_THRESHOLD else 0)

    quantized = blended.convert("P", palette=palette_method, colors=colors)
    raw_palette = quantized.getpalette() or []
    visible_pixels = visible.load()
    pixels = quantized.load()
    used = sorted(
        {
            pixels[x, y]
            for y in range(quantized.height)
            for x in range(quantized.width)
            if visible_pixels[x, y]
        }
    )

    palette = []
    for index in used:
        base = index * 3
        palette.append((raw_palette[base], raw_palette[base + 1], raw_palette[base + 2]))

    remap = {old: new for new, old in enumerate(used)}
    for y in range(quantized.height):
        for x in range(quantized.width):
            pixels[x, y] = remap[pixels[x, y]] if visible_pixels[x, y] else 0
    return quantized, palette, visible


def sixel_color_value(value: int) -> int:
    return round(value * 100 / 255)


def sixel_rle(chars: list[str]) -> str:
    encoded = []
    index = 0
    while index < len(chars):
        char = chars[index]
        count = 1
        while index + count < len(chars) and chars[index + count] == char:
            count += 1
        encoded.append(f"!{count}{char}" if count > 3 else char * count)
        index += count
    return "".join(encoded)


def deco_image_to_sixel(data: bytes, *, width: int = DECO_SIXEL_WIDTH, height: int = DECO_SIXEL_HEIGHT) -> str:
    image = prepare_deco_icon(data, width=width, height=height)
    quantized, palette, visible = quantize_deco_icon_for_sixel(image)
    pixels = quantized.load()
    visible_pixels = visible.load()

    parts = ["\033P0;1q", f'"1;1;{quantized.width};{quantized.height}']
    for index, (red, green, blue) in enumerate(palette):
        parts.append(
            f"#{index};2;{sixel_color_value(red)};{sixel_color_value(green)};{sixel_color_value(blue)}"
        )

    for y in range(0, quantized.height, 6):
        used_colors = sorted(
            {
                pixels[x, yy]
                for yy in range(y, min(y + 6, quantized.height))
                for x in range(quantized.width)
                if visible_pixels[x, yy]
            }
        )
        for color_index in used_colors:
            chars = []
            for x in range(quantized.width):
                bits = 0
                for bit in range(6):
                    yy = y + bit
                    if yy < quantized.height and visible_pixels[x, yy] and pixels[x, yy] == color_index:
                        bits |= 1 << bit
                chars.append(chr(63 + bits))
            parts.append(f"#{color_index}" + sixel_rle(chars) + "$")
        parts.append("-")
    parts.append("\033\\")
    return "".join(parts)


def recipe_quantity_text(quantity: str) -> str:
    if not quantity:
        return ""
    return quantity if quantity.endswith("개") else f"{quantity}개"


def recipe_output_text(name: str, quantity: str) -> str:
    quantity_text = quantity.removesuffix("개").strip()
    return f"{name} ×{quantity_text}" if quantity_text else name


def style_item_name_by_tier(name: str, tier: str) -> str:
    formatted_tier = format_tier(tier)
    style = ITEM_NAME_TIER_STYLES.get(formatted_tier)
    return f"{style}{name}{RESET}" if style else name


def ro_josa(text: str) -> str:
    if not text:
        return "로"
    last_char = text[-1]
    code = ord(last_char)
    if not 0xAC00 <= code <= 0xD7A3:
        return "로"
    jongseong_index = (code - 0xAC00) % 28
    return "로" if jongseong_index in {0, 8} else "으로"


def format_deca(value: int) -> str:
    return f"{value:,}"


def parse_recipe_material_part(raw: str) -> tuple[str, str, int | None]:
    value = raw.strip()
    match = RECIPE_QUANTITY_PATTERN.match(value)
    if not match:
        return value, value, None
    return value, match.group("name").strip(), int(match.group("quantity"))


def recipe_material_parts(recipe: str) -> list[tuple[str, str, int | None]]:
    return [
        parse_recipe_material_part(part)
        for part in recipe.split("//")
        if part.strip()
    ]


def market_price_for_material(name: str, prices: ResistMarketPrices) -> MaterialPrice | None:
    price = prices.items.get(name)
    if price is None or price.status != "exact":
        return None
    return price


def material_market_note(name: str, quantity: int | None, prices: ResistMarketPrices) -> str:
    price = market_price_for_material(name, prices)
    if price is None:
        return ""
    if price.sold_out or price.total_count <= 0:
        return "품절"
    if price.min_price is None:
        return ""
    if quantity is None:
        return f"{format_deca(price.min_price)} D"
    return f"{format_deca(price.min_price)} D  총 {format_deca(price.min_price * quantity)}"


def material_market_total(name: str, quantity: int | None, prices: ResistMarketPrices) -> int | None:
    price = market_price_for_material(name, prices)
    if price is None or quantity is None:
        return None
    if price.sold_out or price.total_count <= 0 or price.min_price is None:
        return None
    return price.min_price * quantity


def recipe_market_total(recipe: str, prices: ResistMarketPrices) -> int:
    return sum(
        total
        for _raw, name, quantity in recipe_material_parts(recipe)
        for total in [material_market_total(name, quantity, prices)]
        if total is not None
    )


def aligned_market_material_lines(recipe: str, prices: ResistMarketPrices, width: int, right_padding: int = 8) -> list[str]:
    rows = [
        (f"· {raw}", material_market_note(name, quantity, prices))
        for raw, name, quantity in recipe_material_parts(recipe)
    ]
    if not rows:
        return [""]
    if not any(note for _material, note in rows):
        return method_wrapped_lines(recipe, width)

    right_padding = max(0, min(right_padding, max(0, width - 12)))
    usable_width = max(1, width - right_padding)
    max_note_width = max(display_width(note) for _material, note in rows)
    note_width = min(max_note_width, max(1, usable_width // 2))
    gap = 2
    material_width = max(8, usable_width - gap - note_width)

    lines = []
    for material, note in rows:
        wrapped_material = wrap_text(material, material_width)
        first = left_cell(wrapped_material[0], material_width) + (" " * gap) + left_cell(note, note_width)
        lines.append(left_cell(first, usable_width) + (" " * right_padding))
        for continuation in wrapped_material[1:]:
            lines.append(left_cell(continuation, usable_width) + (" " * right_padding))
    return lines


def resist_market_recipe_value_lines(key: str, value: str, width: int, prices: ResistMarketPrices) -> list[str]:
    content_width = max(1, width - 2)
    if key == "recipe":
        return aligned_market_material_lines(value, prices, content_width)
    return wrap_text(value, content_width)


def resist_market_card_section_heights(row, attributes: dict[str, str], width: int, prices: ResistMarketPrices) -> dict[str, int]:
    widths = recipe_card_widths(width)
    return {
        key: max(1, len(resist_market_recipe_value_lines(key, value, widths[1], prices)))
        for key, label, value, value_align in recipe_card_rows(row, attributes)
    }


def market_cost_text(total: int) -> str:
    return f"총 {format_deca(total)} D (품절 제외)"


def market_basis_timestamp(prices: ResistMarketPrices) -> str:
    return prices.updated_at or prices.cache_updated_at


def market_basis_value_widths(value_width: int, timestamp: str, cost_text: str) -> list[int]:
    available = max(1, value_width - 1)
    left = min(max(display_width(timestamp) + 2, available // 2), max(1, available - 1))
    right = available - left
    min_right = display_width(cost_text) + 2
    if right < min_right and available > min_right:
        right = min(available - 1, min_right)
        left = available - right
    return [left, right]


def render_labeled_split_value_row(label: str, left_value: str, right_value: str, widths: list[int]) -> tuple[list[str], list[int]]:
    value_widths = market_basis_value_widths(widths[1], left_value, right_value)
    left_lines = wrap_text(left_value, max(1, value_widths[0] - 2))
    right_lines = wrap_text(right_value, max(1, value_widths[1] - 2))
    height = max(len(left_lines), len(right_lines), 1)
    left_lines += [""] * (height - len(left_lines))
    right_lines += [""] * (height - len(right_lines))
    label_index = (height - 1) // 2

    rendered = []
    for index in range(height):
        rendered.append(
            "│"
            + center_cell(label if index == label_index else "", widths[0])
            + "│"
            + center_cell(left_lines[index], value_widths[0])
            + "│"
            + center_cell(right_lines[index], value_widths[1])
            + "│"
        )
    return rendered, value_widths

def recipe_card_rows(row, attributes: dict[str, str]) -> list[tuple[str, str, str, str]]:
    recipe = row["description"] or attributes.get("레시피", "")
    rows = [("name", "이름", style_item_name_by_tier(row["name"], attributes.get("등급", "")), "center")]

    if attributes.get("종류"):
        rows.append(("type", "종류", attributes["종류"], "center"))
    if attributes.get("제작대"):
        rows.append(("workbench", "제작대", attributes["제작대"], "center"))
    if attributes.get("시간"):
        rows.append(("time", "가공 시간", attributes["시간"], "center"))
    if attributes.get("생산량"):
        rows.append(("output_qty", "제작 결과", recipe_output_text(row["name"], attributes["생산량"]), "center"))
    if recipe:
        rows.append(("recipe", "제작 재료", recipe, "left"))

    return rows


def recipe_value_lines(key: str, value: str, width: int) -> list[str]:
    content_width = max(1, width - 2)
    if key == "recipe":
        return method_wrapped_lines(value, content_width)
    return wrap_text(value, content_width)


def recipe_card_section_heights(row, attributes: dict[str, str], width: int) -> dict[str, int]:
    widths = recipe_card_widths(width)
    return {
        key: max(1, len(recipe_value_lines(key, value, widths[1])))
        for key, label, value, value_align in recipe_card_rows(row, attributes)
    }


def render_recipe_result_card(
    row,
    attributes: dict[str, str],
    width: int,
    section_heights: dict[str, int] | None = None,
) -> list[str]:
    widths = recipe_card_widths(width)
    rows = recipe_card_rows(row, attributes)
    heights = section_heights or recipe_card_section_heights(row, attributes, width)
    lines = [hline("┌", "┬", "┐", widths)]
    for index, (key, label, value, value_align) in enumerate(rows):
        lines.extend(
            render_labeled_value_row_fixed(
                label,
                recipe_value_lines(key, value, widths[1]),
                widths,
                value_align,
                heights[key],
            )
        )
        if index == len(rows) - 1:
            lines.append(hline("└", "┴", "┘", widths))
        else:
            lines.append(hline("├", "┼", "┤", widths))
    return lines


def print_recipe_result_cards(rows, conn) -> None:
    width = terminal_width()
    gap = "   "
    use_two_columns = width >= 96
    card_width = (width - display_width(gap)) // 2 if use_two_columns else min(70, width)
    entries = [
        (row, attributes_to_dict(get_attributes(conn, row["id"])))
        for row in rows
    ]
    cards = [
        render_recipe_result_card(row, attributes, card_width)
        for row, attributes in entries
    ]
    print_card_grid(cards, card_width, use_two_columns=use_two_columns)


def render_resist_market_result_card(
    row,
    attributes: dict[str, str],
    width: int,
    prices: ResistMarketPrices,
) -> list[str]:
    if not prices.ok:
        return render_recipe_result_card(row, attributes, width)

    widths = recipe_card_widths(width)
    rows = recipe_card_rows(row, attributes)
    heights = resist_market_card_section_heights(row, attributes, width, prices)
    timestamp = market_basis_timestamp(prices)
    recipe = row["description"] or attributes.get("레시피", "")
    show_basis = bool(timestamp and recipe)
    total = recipe_market_total(recipe, prices)
    cost_text = market_cost_text(total)

    lines = [hline("┌", "┬", "┐", widths)]
    for index, (key, label, value, value_align) in enumerate(rows):
        lines.extend(
            render_labeled_value_row_fixed(
                label,
                resist_market_recipe_value_lines(key, value, widths[1], prices),
                widths,
                value_align,
                heights[key],
            )
        )
        if index == len(rows) - 1:
            if show_basis:
                basis_widths = market_basis_value_widths(widths[1], timestamp, cost_text)
                lines.append(hline_with_joins("├", ["┼", "┬"], "┤", [widths[0], *basis_widths]))
                basis_lines, basis_widths = render_labeled_split_value_row("시세 기준", timestamp, cost_text, widths)
                lines.extend(basis_lines)
                lines.append(hline_with_joins("└", ["┴", "┴"], "┘", [widths[0], *basis_widths]))
            else:
                lines.append(hline("└", "┴", "┘", widths))
        else:
            lines.append(hline("├", "┼", "┤", widths))
    return lines


def print_resist_result_cards(rows, conn, prices: ResistMarketPrices) -> None:
    card_width = min(70, terminal_width())
    entries = [
        (row, attributes_to_dict(get_attributes(conn, row["id"])))
        for row in rows
    ]
    cards = [
        render_resist_market_result_card(row, attributes, card_width, prices)
        for row, attributes in entries
    ]
    print_card_grid(cards, card_width, use_two_columns=False)

def render_deco_image_name_row(name: str, widths: list[int], *, height: int = DECO_IMAGE_ROW_HEIGHT) -> list[str]:
    name_index = height // 2
    rendered = []
    for index in range(height):
        value = name if index == name_index else ""
        rendered.append("│" + center_cell("", widths[0]) + "│" + center_cell(value, widths[1]) + "│")
    return rendered


def deco_sixel_cursor_offset(widths: list[int]) -> tuple[int, int]:
    down = 1 + max(0, (DECO_IMAGE_ROW_HEIGHT - DECO_IMAGE_CELL_ROWS + 1) // 2)
    right = 1 + max(0, (widths[0] - DECO_IMAGE_CELL_COLUMNS + 1) // 2)
    return down, right


def render_deco_image_result_card(row, attributes: dict[str, str], width: int) -> list[str]:
    widths = recipe_card_widths(width)
    name = style_item_name_by_tier(row["name"], attributes.get("등급", ""))
    rows = [entry for entry in recipe_card_rows(row, attributes) if entry[0] != "name"]

    lines = [hline("┌", "┬", "┐", widths)]
    lines.extend(render_deco_image_name_row(name, widths))
    if not rows:
        lines.append(hline("└", "┴", "┘", widths))
        return lines

    lines.append(hline("├", "┼", "┤", widths))
    for index, (key, label, value, value_align) in enumerate(rows):
        value_lines = recipe_value_lines(key, value, widths[1])
        min_height = DECO_RECIPE_ROW_HEIGHT if key == "recipe" else 1
        lines.extend(
            render_labeled_value_row_fixed(
                label,
                value_lines,
                widths,
                value_align,
                max(min_height, len(value_lines)),
            )
        )
        if index == len(rows) - 1:
            lines.append(hline("└", "┴", "┘", widths))
        else:
            lines.append(hline("├", "┼", "┤", widths))
    return lines


def maybe_deco_sixel_payload(row, attributes: dict[str, str]) -> str | None:
    data = deco_asset_bytes(row, attributes)
    if data is None:
        return None
    try:
        return deco_image_to_sixel(data)
    except Exception:
        return None


def print_deco_image_card_grid(
    cards: list[tuple[list[str], str | None]],
    card_width: int,
    *,
    use_two_columns: bool,
) -> None:
    gap = "   "
    step = 2 if use_two_columns else 1
    widths = recipe_card_widths(card_width)
    image_down, image_right = deco_sixel_cursor_offset(widths)
    image_draw_line = image_down + DECO_IMAGE_CELL_ROWS - 1

    for index in range(0, len(cards), step):
        left_lines, left_payload = cards[index]
        right = cards[index + 1] if use_two_columns and index + 1 < len(cards) else None
        right_lines = right[0] if right is not None else None
        right_payload = right[1] if right is not None else None

        row_count = max(len(left_lines), len(right_lines) if right_lines is not None else 0)
        blank = " " * card_width
        for line_index in range(row_count):
            left_line = left_lines[line_index] if line_index < len(left_lines) else blank
            if right_lines is None:
                sys.stdout.write(left_line + "\n")
            else:
                right_line = right_lines[line_index] if line_index < len(right_lines) else blank
                sys.stdout.write(pad_line(left_line, card_width) + gap + right_line + "\n")

            if line_index == image_draw_line:
                up = line_index + 1 - image_down
                for side, payload in enumerate((left_payload, right_payload)):
                    if not payload:
                        continue
                    right_offset = image_right + (card_width + display_width(gap)) * side
                    sys.stdout.write("\033[s")
                    sys.stdout.write(f"\033[{up}A\r\033[{right_offset}C")
                    sys.stdout.write(payload)
                    sys.stdout.write("\033[u")
        sys.stdout.flush()

        if index + step < len(cards):
            print()
            print()


def print_deco_result_cards(rows, conn) -> None:
    width = terminal_width()
    gap = "   "
    use_two_columns = width >= 96
    card_width = (width - display_width(gap)) // 2 if use_two_columns else min(70, width)
    entries = [
        (row, attributes_to_dict(get_attributes(conn, row["id"])))
        for row in rows
    ]

    if not DECO_IMAGE_CARDS_ENABLED:
        cards = [
            render_recipe_result_card(row, attributes, card_width)
            for row, attributes in entries
        ]
        print_card_grid(cards, card_width, use_two_columns=use_two_columns)
        return

    cards_with_images = []
    for row, attributes in entries:
        payload = maybe_deco_sixel_payload(row, attributes)
        if payload is None:
            cards_with_images.append((render_recipe_result_card(row, attributes, card_width), None))
        else:
            cards_with_images.append((render_deco_image_result_card(row, attributes, card_width), payload))
    print_deco_image_card_grid(cards_with_images, card_width, use_two_columns=use_two_columns)


def recipe_usage_page(rows, page: int) -> tuple[list, int, bool]:
    start = max(0, page) * USAGE_PAGE_SIZE
    page_rows = rows[start : start + USAGE_PAGE_SIZE]
    return page_rows, start, start + len(page_rows) < len(rows)


def print_recipe_usage_columns(rows, conn, start_index: int) -> None:
    lines = []
    for index, row in enumerate(rows, start_index + 1):
        attributes = attributes_to_dict(get_attributes(conn, row["id"]))
        lines.append(f"{index}. {style_item_name_by_tier(row['name'], attributes.get('등급', ''))}")

    first_column = lines[:USAGE_COLUMN_SIZE]
    second_column = lines[USAGE_COLUMN_SIZE:]
    gap = "   "
    max_width = max((display_width(line) for line in lines), default=0)
    column_width = min(max_width, max(20, (terminal_width() - display_width(gap)) // USAGE_COLUMN_COUNT))
    row_count = max(len(first_column), len(second_column))
    for index in range(row_count):
        left = first_column[index] if index < len(first_column) else ""
        right = second_column[index] if index < len(second_column) else ""
        if right:
            print(pad_line(left, column_width) + gap + right)
        else:
            print(left)
    print()
    print()


def print_recipe_usage_results(
    rows,
    conn,
    keyword: str,
    result_label: str = "제작물",
    *,
    page: int = 0,
) -> tuple[list, bool]:
    page_rows, start, has_next_page = recipe_usage_page(rows, page)
    if len(rows) > USAGE_PAGE_SIZE:
        count_text = f"{len(rows)}건 / {start + 1}-{start + len(page_rows)} 표시"
    else:
        count_text = f"{len(rows)}건"
    print_left_box([f"{keyword}{ro_josa(keyword)} 만들 수 있는 {result_label}", count_text])
    print_recipe_usage_columns(page_rows, conn, start)
    return page_rows, has_next_page


def print_recipe_results(conn, keyword: str, scope_label: str, usage_page: int = 0):
    direct_rows = search_recipe_entries_by_name(conn, keyword, 20)
    direct_ids = {row["id"] for row in direct_rows}
    usage_rows = [
        row
        for row in search_recipe_entries_by_ingredient(conn, keyword, None)
        if row["id"] not in direct_ids
    ]
    rows = [*direct_rows, *usage_rows]

    print_header("mabiDB", scope_label)
    print(f"검색어: {keyword}")
    print(f"결과: 제작법 {len(direct_rows)}건 / 재료 사용 {len(usage_rows)}건")
    print()

    if not rows:
        print_full_box(["검색 결과가 없습니다."])
        return rows, False

    if direct_rows and usage_page == 0:
        print_recipe_result_cards(direct_rows, conn)
        if usage_rows:
            print()
            print()
    visible_rows = direct_rows if usage_page == 0 else []
    has_next_page = False
    if usage_rows:
        visible_usage_rows, has_next_page = print_recipe_usage_results(usage_rows, conn, keyword, page=usage_page)
        visible_rows = [*visible_rows, *visible_usage_rows]
    return visible_rows, has_next_page


def print_resist_results(conn, keyword: str, scope_label: str, usage_page: int = 0):
    direct_rows = search_resist_entries_by_name(conn, keyword, 20)
    direct_ids = {row["id"] for row in direct_rows}
    usage_rows = [
        row
        for row in search_resist_entries_by_ingredient(conn, keyword, None)
        if row["id"] not in direct_ids
    ]
    rows = [*direct_rows, *usage_rows]

    print_header("mabiDB", scope_label)
    print(f"검색어: {keyword}")
    print(f"결과: 마도저항 {len(direct_rows)}건 / 재료 사용 {len(usage_rows)}건")
    print()

    if not rows:
        print_full_box(["검색 결과가 없습니다."])
        return rows, False

    if direct_rows and usage_page == 0:
        print_resist_result_cards(direct_rows, conn, fetch_resist_market_prices())
        if usage_rows:
            print()
            print()
    visible_rows = direct_rows if usage_page == 0 else []
    has_next_page = False
    if usage_rows:
        visible_usage_rows, has_next_page = print_recipe_usage_results(usage_rows, conn, keyword, "마도저항", page=usage_page)
        visible_rows = [*visible_rows, *visible_usage_rows]
    return visible_rows, has_next_page


def print_deco_results(conn, keyword: str, scope_label: str, usage_page: int = 0):
    direct_rows = search_deco_entries_by_name(conn, keyword, None)
    direct_ids = {row["id"] for row in direct_rows}
    usage_rows = [
        row
        for row in search_deco_entries_by_ingredient(conn, keyword, None)
        if row["id"] not in direct_ids
    ]
    rows = [*direct_rows, *usage_rows]

    print_header("mabiDB", scope_label)
    print(f"검색어: {keyword}")
    print(f"결과: 데코 제작법 {len(direct_rows)}건 / 재료 사용 {len(usage_rows)}건")
    print()

    if not rows:
        print_full_box(["검색 결과가 없습니다."])
        return rows, False

    if direct_rows and usage_page == 0:
        print_deco_result_cards(direct_rows, conn)
        if usage_rows:
            print()
            print()
    visible_rows = direct_rows if usage_page == 0 else []
    has_next_page = False
    if usage_rows:
        visible_usage_rows, has_next_page = print_recipe_usage_results(usage_rows, conn, keyword, "데코", page=usage_page)
        visible_rows = [*visible_rows, *visible_usage_rows]
    return visible_rows, has_next_page


def build_update_result_lines(app_update_result, db_update_result, deco_update_result=None) -> list[str]:
    lines = ["업데이트 결과"]

    def append_result(label, result, fallback, *, status_override=None, message_override=None) -> None:
        if result is None:
            return
        status = status_override or result.status
        message = result.message if message_override is None else message_override
        if status == "updated":
            lines.extend([f"✔️ {label} 업데이트 완료", f"   기준일자 : {message}"])
        elif status == "unchanged":
            lines.append(f"✔️ {label} 최신버전입니다.")
        elif status == "failed":
            lines.extend([f"! {label} 업데이트 확인 실패", f"   {fallback}"])
        lines.append("")

    append_result("앱", app_update_result, "기존 앱으로 실행합니다.")

    db_status_override = None
    db_message_override = None
    if (
        db_update_result is not None
        and deco_update_result is not None
        and db_update_result.status == "unchanged"
        and deco_update_result.status == "updated"
    ):
        db_status_override = "updated"
        db_message_override = db_update_result.message or deco_update_result.message
    append_result(
        "DB",
        db_update_result,
        "기존 DB로 실행합니다.",
        status_override=db_status_override,
        message_override=db_message_override,
    )

    if deco_update_result is not None and deco_update_result.status == "failed":
        append_result("데코 이미지", deco_update_result, "기존 텍스트 카드로 실행합니다.")

    while lines and lines[-1] == "":
        lines.pop()
    return lines


def feedback_reply_summary_lines(feedback_result) -> list[str]:
    lines = ["내 수정 요청 답변"]
    if feedback_result is None:
        lines.append("답변 확인 안 함")
        return lines
    if feedback_result.status == "ok":
        total = len(feedback_result.threads)
        answered = sum(1 for thread in feedback_result.threads if thread.replies)
        unread = unread_feedback_reply_count(feedback_result.threads)
        if total == 0:
            lines.append("답변 내역 없음")
        elif unread > 0:
            lines.append(f"새 답변 {unread}건")
        else:
            lines.append(f"답변 완료 {answered}건 / 전체 {total}건")
        lines.append("0: 답변 확인")
    elif feedback_result.status == "failed":
        lines.extend(["답변 확인 실패", "0: 다시 확인"])
    else:
        lines.append("답변 기능 비활성")
    return lines


def print_update_results(app_update_result, db_update_result, deco_update_result=None) -> None:
    lines = build_update_result_lines(app_update_result, db_update_result, deco_update_result)
    if len(lines) > 1:
        print_left_box(lines)
        print()


def render_left_box(lines: list[str], content_width: int | None = None) -> list[str]:
    if content_width is None:
        content_width = max(display_width(strip_ansi(line)) for line in lines) + 2
    rendered = ["┌" + ("─" * content_width) + "┐"]
    for index, line in enumerate(lines):
        if index == 1:
            rendered.append("├" + ("─" * content_width) + "┤")
        for wrapped in wrap_text(line, content_width - 2):
            rendered.append("│ " + left_cell(wrapped, content_width - 2) + " │")
    rendered.append("└" + ("─" * content_width) + "┘")
    return rendered


def print_left_box(lines: list[str]) -> None:
    for line in render_left_box(lines):
        print(line)


def print_box_row(left_lines: list[str], right_lines: list[str]) -> None:
    gap = "    "
    left_width = max(display_width(strip_ansi(line)) for line in left_lines)
    height = max(len(left_lines), len(right_lines))
    blank_left = " " * left_width
    for index in range(height):
        left = left_lines[index] if index < len(left_lines) else blank_left
        right = right_lines[index] if index < len(right_lines) else ""
        print(pad_line(left, left_width) + gap + right)


def print_startup_cards(app_update_result, db_update_result, deco_update_result, feedback_result) -> None:
    update_lines = build_update_result_lines(app_update_result, db_update_result, deco_update_result)
    if len(update_lines) <= 1:
        update_lines = []
    reply_lines = feedback_reply_summary_lines(feedback_result) if feedback_result is not None else []

    if not update_lines and not reply_lines:
        return
    if update_lines and reply_lines:
        update_box = render_left_box(update_lines)
        reply_box = render_left_box(reply_lines)
        total_width = max(display_width(strip_ansi(line)) for line in update_box)
        total_width += 4 + max(display_width(strip_ansi(line)) for line in reply_box)
        if total_width <= terminal_width():
            print_box_row(update_box, reply_box)
            print()
            return
    if update_lines:
        print_left_box(update_lines)
        print()
    if reply_lines:
        print_left_box(reply_lines)
        print()


def prompt_feedback_history_action(threads=None) -> None:
    choice = input("Enter를 누르면 돌아갑니다 / D 입력: 기록 지우기 > ").strip().casefold()
    if choice != "d":
        return

    confirm = input("이전 수정 요청과 답변 기록을 삭제합니다. 지워진 내역은 복구 할 수 없습니다. 지우려면 DELETE 입력 > ").strip()
    if confirm != "DELETE":
        print("기록 지우기를 취소했습니다.")
    else:
        try:
            clear_feedback_history(threads or [])
            print("기록을 삭제했습니다.")
        except OSError as exc:
            print(f"기록 지우기 실패: {exc}")
    input("Enter를 누르면 돌아갑니다 > ")

def show_feedback_replies() -> None:
    result = fetch_revision_replies()
    print_header("mabiDB", "내 수정 요청 답변")
    if result.status != "ok":
        print_full_box(["답변을 불러오지 못했습니다."])
        if result.message:
            print(result.message)
        print()
        prompt_feedback_history_action()
        return

    if not result.threads:
        print_full_box(["아직 보낸 수정 요청이나 답변이 없습니다."])
        print()
        prompt_feedback_history_action()
        return

    threads = sorted(result.threads, key=lambda thread: thread.created_at)
    for thread in threads:
        lines = [
            f"요청 ID: {thread.request_id or '-'}",
            f"상태: {'답변 완료' if thread.replies else '답변 대기'}",
            f"요청 시각: {thread.created_at or '-'}",
            f"검색범위: {thread.search_scope or '-'}",
            f"검색어: {thread.search_query or '-'}",
            "요청내용:",
            thread.message or "-",
        ]
        if thread.replies:
            replies = sorted(thread.replies, key=lambda reply: reply.created_at)
            for reply in replies:
                lines.extend(["", f"답변 시각: {reply.created_at or '-'}", "답변:", reply.message or "-"])
        else:
            lines.extend(["", "답변:", "아직 답변이 없습니다."])
        print_left_box(lines)
        print()
    mark_feedback_replies_seen(result.threads)
    prompt_feedback_history_action(result.threads)

def print_results(conn, keyword: str, scope: str, scope_label: str, usage_page: int = 0):
    if scope == "recipe":
        return print_recipe_results(conn, keyword, scope_label, usage_page)
    if scope == "deco":
        return print_deco_results(conn, keyword, scope_label, usage_page)
    if scope == "resist":
        return print_resist_results(conn, keyword, scope_label, usage_page)

    rows = search_entries(conn, keyword, 20, scope)

    print_header("mabiDB", scope_label)
    print(f"검색어: {keyword}")
    print(f"결과: {len(rows)}건")
    if scope == "gathering":
        print("황금 재료는 '황금'을 검색해주세요")
    print()

    if not rows:
        print_full_box(["검색 결과가 없습니다."])
        return rows, False

    if scope == "gathering":
        print_gathering_result_cards(rows, conn)
        return rows, False
    if scope == "barter":
        print_barter_result_cards(rows, conn, keyword)
        return rows, False
    print_result_cards(rows, conn)
    return rows, False


def recent_result_items(rows, *, limit: int = 5) -> list[dict[str, object]]:
    items = []
    for row in rows[:limit]:
        item_type = row["type"]
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "type": item_type,
                "type_label": TYPE_LABELS.get(item_type, item_type),
            }
        )
    return items


def prompt_revision_request(keyword: str, scope: str, scope_label: str, rows) -> str:
    print_header("mabiDB", scope_label)
    print_full_box(["수정 요청"])
    print("잘못된 정보나 수정요청 내용을 적어주세요! 상세히 적어주시면 좋아요ㅠ")
    print("취소하려면 q 또는 ㅂ을 입력하거나 그냥 Enter를 누르세요.")
    print()
    print(f"검색어: {keyword}")
    print()

    recent_results = recent_result_items(rows)
    if recent_results:
        print("최근 검색 결과")
        for index, item in enumerate(recent_results, 1):
            print(f"  {index}. {item['name']} ({item['type_label']})")
        print()
    else:
        print("최근 검색 결과가 없습니다. 누락된 정보 요청이면 내용을 적어주세요.")
        print()

    message = input("내용 > ").strip()
    if not message or message.lower() in {"q", "ㅂ"}:
        return "수정 요청이 취소되었습니다."

    report = build_revision_request(
        scope=scope,
        scope_label=scope_label,
        keyword=keyword,
        message=message,
        recent_results=recent_results,
    )
    result = submit_revision_request(report)
    if result.status == "sent":
        if result.request_id:
            return f"✔ 수정 요청이 접수되었습니다. 요청 ID: {result.request_id}"
        return "✔ 수정 요청이 접수되었습니다. 감사합니다."
    return "! 수정 요청 전송에 실패했습니다. 잠시 후 다시 시도해주세요."


def search_loop(scope: str, scope_label: str, feedback_result=None) -> None:
    with connect() as conn:
        while True:
            print_header("mabiDB", scope_label)
            print(f"{search_help_text(scope)}\n")
            print("\nEnter:검색  1:뒤로가기  2:종료")
            print()
            keyword = input("검색어 > ").strip()
            if is_quit_command(keyword):
                return
            if is_back_command(keyword):
                new_scope, new_label, feedback_result = choose_scope(feedback_result=feedback_result)
                scope, scope_label = new_scope, new_label
                continue
            if not keyword:
                continue

            status_message = ""
            usage_page = 0
            while True:
                rows, has_next_page = print_results(conn, keyword, scope, scope_label, usage_page)
                if status_message:
                    print(status_message)
                    print()
                    status_message = ""
                page_commands = []
                if usage_page > 0:
                    page_commands.append(f"{PREVIOUS_PAGE_COMMAND}:이전 페이지")
                if has_next_page:
                    page_commands.append(f"{NEXT_PAGE_COMMAND}:다음 페이지")
                page_help = ("  " + "  ".join(page_commands)) if page_commands else ""
                print(f"Enter:검색{page_help}  0:수정 요청  1:뒤로가기  2:종료\n")
                next_input = input("검색어 > ").strip()
                if is_quit_command(next_input):
                    return
                if next_input == PREVIOUS_PAGE_COMMAND:
                    if usage_page > 0:
                        usage_page -= 1
                    else:
                        status_message = "이전 페이지가 없습니다."
                    continue
                if next_input == NEXT_PAGE_COMMAND:
                    if has_next_page:
                        usage_page += 1
                    else:
                        status_message = "다음 페이지가 없습니다."
                    continue
                if is_revision_request_command(next_input):
                    status_message = prompt_revision_request(keyword, scope, scope_label, rows)
                    continue
                if is_back_command(next_input, allow_b=True):
                    scope, scope_label, feedback_result = choose_scope(feedback_result=feedback_result)
                    break
                if not next_input:
                    break
                keyword = next_input
                usage_page = 0


def run_tui() -> None:
    global DECO_IMAGE_CARDS_ENABLED

    configure_console()
    if relaunch_in_windows_terminal_if_needed():
        return
    ensure_local_version_files()
    completed_app_update = read_completed_app_update()
    if completed_app_update is not None:
        app_update_result = completed_app_update
    else:
        app_update_result = update_app_from_remote()
        if app_update_result.status == "restarting":
            return
    update_result = initialize(update_remote=True)
    deco_update_result = update_deco_assets_from_remote()
    DECO_IMAGE_CARDS_ENABLED = detect_deco_image_card_support()
    feedback_result = fetch_revision_replies()
    print("앱 시작")
    scope, scope_label, feedback_result = choose_scope(update_result, app_update_result, deco_update_result, feedback_result)
    search_loop(scope, scope_label, feedback_result)
    print()
    print(f"DB: {DB_PATH}")


def main() -> None:
    handle_app_update_args(sys.argv[1:])
    run_tui()


if __name__ == "__main__":
    main()


