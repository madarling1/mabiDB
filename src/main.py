from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata

from app_updater import handle_app_update_args, read_completed_app_update, update_app_from_remote
from database import DB_PATH, connect, ensure_local_version_files, initialize
from reporter import build_revision_request, submit_revision_request
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
)


SCOPES = {
    "1": ("equipment", "무기 / 방어구 / 엠블럼"),
    "2": ("accessory", "장신구"),
    "3": ("gathering", "생활 채집"),
    "4": ("barter", "물물교환"),
    "5": ("recipe", "제작법"),
    "6": ("deco", "데코 제작법"),
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
URL_PATTERN = re.compile(r"https?://\S+")
DESCRIPTION_BREAK_PATTERN = re.compile(r"(?<!:)//")
ANSI_PATTERN = re.compile(r"\033\[[0-9;]*m")


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


def choose_scope(update_result=None, app_update_result=None) -> tuple[str, str]:
    update_message_pending = update_result is not None or app_update_result is not None
    error_message = ""
    while True:
        print_header("mabiDB")
        if update_message_pending:
            print_update_results(app_update_result, update_result)
            update_message_pending = False
        print("검색할 그룹을 선택하세요.\n\n초성 검색,영문검색을 지원합니다!\n  ex) ㅇㄷㅎㅂ > 아득한빛\n  ex) dkemr > 아득")
        print()
        print("  1. 무기 / 방어구 / 엠블럼 룬")
        print("  2. 장신구 룬")
        print("  3. 생활 채집")
        print("  4. 물물교환")
        print("  5. 제작법")
        print("  6. 데코 제작법")
        print()
        if error_message:
            print(error_message)
            print()
            error_message = ""
        choice = input("번호 입력 > ").strip()
        if choice in SCOPES:
            return SCOPES[choice]
        error_message = "1, 2, 3, 4, 5, 6 중 하나를 입력하세요."


def search_help_text(scope: str) -> str:
    if scope == "gathering":
        return "채집물 이름으로 검색 가능합니다. 예) 양털, 마나석 등\n초성검색도 가능합니다. ex) ㄷㄲㅇㅇㅌ > 두꺼운양털\n\n# 황금 재료는 '황금'을 검색해주세요"
    if scope == "barter":
        return "NPC명, 아이템명, 지역으로 검색 가능합니다. 예) 말콤, 상급 양털, 티르코네일\n초성검색도 가능합니다. ex) ㅁㅋ > 말콤"
    if scope == "recipe":
        return "아이템명, 재료명으로 검색 가능합니다. 예) 금은매운탕, 상급 양털\n초성검색도 가능합니다. ex) ㅊㄱ > 철괴"
    if scope == "deco":
        return "데코명, 재료명으로 검색 가능합니다. 예) 협탁, 목재, 데코 제작 부품\n초성검색도 가능합니다. ex) ㅎㅌ > 협탁"
    if scope == "accessory":
        return "이름, 클래스, 설명으로 검색 가능합니다. 예) 관통, 기사, 홀리스피어\n초성검색도 가능합니다. ex) ㅅㄹㅂㅋ > 수레바퀴"
    return "이름, 내용, 태그, 줄임말로 검색 가능합니다. 예) 쏟불, 무방비, 주피증\n초성검색도 가능합니다. ex) ㅇㄷㅎㅂ > 아득한빛"


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

    return {
        "name": len(render_wrapped_row(["이름", row["name"]], widths, ["center", "center"])),
        "tag": len(render_wrapped_row(["분류", attributes.get("태그", "")], widths, ["center", "center"])),
        "location": len(render_wrapped_row(["채집 장소", attributes.get("위치", "")], widths, ["center", "center"])),
        "method": len(render_labeled_lines_row_fixed("채집 방법", method_lines, widths, height=1)),
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

    lines = [hline("┌", "┬", "┐", widths)]
    lines.extend(render_wrapped_row_fixed(["이름", colored_name], widths, ["center", "center"], heights["name"]))
    lines.append(hline("├", "┼", "┤", widths))
    lines.extend(render_wrapped_row_fixed(["분류", attributes.get("태그", "")], widths, ["center", "center"], heights["tag"]))
    lines.append(hline("├", "┼", "┤", widths))
    lines.extend(render_wrapped_row_fixed(["채집 장소", attributes.get("위치", "")], widths, ["center", "center"], heights["location"]))
    lines.append(hline("├", "┼", "┤", widths))
    lines.extend(render_labeled_lines_row_fixed("채집 방법", method_lines, widths, height=heights["method"]))
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
    section_heights = max_gathering_card_heights(entries, card_width)
    cards = [
        render_gathering_result_card(row, attributes, card_width, section_heights)
        for row, attributes in entries
    ]
    print_card_grid(cards, card_width, use_two_columns=use_two_columns)


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


def recipe_card_rows(row, attributes: dict[str, str]) -> list[tuple[str, str, str, str]]:
    recipe = row["description"] or attributes.get("레시피", "")
    rows = [("name", "이름", style_item_name_by_tier(row["name"], attributes.get("등급", "")), "center")]

    if attributes.get("종류"):
        rows.append(("type", "종류", attributes["종류"], "center"))
    if attributes.get("제작대"):
        rows.append(("workbench", "제작대", attributes["제작대"], "center"))
    if attributes.get("시간"):
        rows.append(("time", "제작 시간", attributes["시간"], "center"))
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


def print_deco_results(conn, keyword: str, scope_label: str, usage_page: int = 0):
    direct_rows = search_deco_entries_by_name(conn, keyword, 20)
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
        print_recipe_result_cards(direct_rows, conn)
        if usage_rows:
            print()
            print()
    visible_rows = direct_rows if usage_page == 0 else []
    has_next_page = False
    if usage_rows:
        visible_usage_rows, has_next_page = print_recipe_usage_results(usage_rows, conn, keyword, "데코", page=usage_page)
        visible_rows = [*visible_rows, *visible_usage_rows]
    return visible_rows, has_next_page


def print_update_results(app_update_result, db_update_result) -> None:
    lines = ["업데이트 결과"]
    for label, result, fallback in (
        ("앱", app_update_result, "기존 앱으로 실행합니다."),
        ("DB", db_update_result, "기존 DB로 실행합니다."),
    ):
        if result is None:
            continue
        if result.status == "updated":
            lines.extend([f"✔️ {label} 업데이트 완료", f"   기준일자 : {result.message}"])
        elif result.status == "unchanged":
            lines.append(f"✔️ {label} 최신버전입니다.")
        elif result.status == "failed":
            lines.extend([f"! {label} 업데이트 확인 실패", f"   {fallback}"])
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    if len(lines) > 1:
        print_left_box(lines)
        print()


def print_left_box(lines: list[str]) -> None:
    content_width = max(display_width(line) for line in lines) + 2
    print("┌" + ("─" * content_width) + "┐")
    for index, line in enumerate(lines):
        if index == 1:
            print("├" + ("─" * content_width) + "┤")
        for wrapped in wrap_text(line, content_width - 2):
            print("│ " + left_cell(wrapped, content_width - 2) + " │")
    print("└" + ("─" * content_width) + "┘")


def print_results(conn, keyword: str, scope: str, scope_label: str, usage_page: int = 0):
    if scope == "recipe":
        return print_recipe_results(conn, keyword, scope_label, usage_page)
    if scope == "deco":
        return print_deco_results(conn, keyword, scope_label, usage_page)

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
    print("잘못된 정보나 수정요청 내용을 적어주세요!")
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
        return "✔ 수정 요청이 접수되었습니다. 감사합니다."
    return "! 수정 요청 전송에 실패했습니다. 잠시 후 다시 시도해주세요."


def search_loop(scope: str, scope_label: str) -> None:
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
                new_scope, new_label = choose_scope()
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
                    scope, scope_label = choose_scope()
                    break
                if not next_input:
                    break
                keyword = next_input
                usage_page = 0


def run_tui() -> None:
    configure_console()
    ensure_local_version_files()
    completed_app_update = read_completed_app_update()
    if completed_app_update is not None:
        app_update_result = completed_app_update
    else:
        app_update_result = update_app_from_remote()
        if app_update_result.status == "restarting":
            return
    update_result = initialize(update_remote=True)
    print("앱 시작")
    scope, scope_label = choose_scope(update_result, app_update_result)
    search_loop(scope, scope_label)
    print()
    print(f"DB: {DB_PATH}")


def main() -> None:
    handle_app_update_args(sys.argv[1:])
    run_tui()


if __name__ == "__main__":
    main()
