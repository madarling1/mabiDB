from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from database import DB_PATH, connect, initialize
from search import get_attributes, search_entries


SCOPES = {
    "1": ("equipment", "무기 / 방어구 / 엠블럼"),
    "2": ("accessory", "장신구"),
    "3": ("gathering", "생활 채집"),
}

TYPE_LABELS = {
    "WeaponRune": "무기",
    "ArmorRune": "방어구",
    "EmblemRune": "엠블럼",
    "AccessoryRune": "장신구",
    "Item": "아이템",
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
TIER_STYLES = {
    "전설": "\033[91m",
    "신화": "\033[93m",
    "엘리트": "\033[38;5;135m",
    "에픽": "\033[38;5;199m",
}
QUIT_COMMANDS = {"2", "/q", "/quit", "q", "quit", "exit"}
BACK_COMMANDS = {"1", "/back", "back"}
URL_PATTERN = re.compile(r"https?://\S+")
DESCRIPTION_BREAK_PATTERN = re.compile(r"(?<!:)//")


def configure_console() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def char_width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


def display_width(text: object) -> int:
    return sum(char_width(char) for char in str(text))


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


def center_tier_cell(text: object, width: int) -> str:
    value = fit_cell(text, width).rstrip()
    padding = max(0, width - display_width(value))
    left = padding // 2
    right = padding - left
    return (" " * left) + style_tier_text(value) + (" " * right)


def strip_ansi(text: object) -> str:
    value = str(text)
    result = ""
    index = 0
    while index < len(value):
        if value[index] == "\033":
            end = value.find("m", index)
            if end == -1:
                break
            index = end + 1
            continue
        result += value[index]
        index += 1
    return result


def wrap_text(text: object, width: int) -> list[str]:
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in value.split("\n"):
        current = ""
        used = 0
        for char in raw_line:
            width_for_char = char_width(char)
            if used + width_for_char > width:
                lines.append(current)
                current = char
                used = width_for_char
            else:
                current += char
                used += width_for_char
        lines.append(current)
    return lines or [""]


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def terminal_width() -> int:
    columns = shutil.get_terminal_size((110, 30)).columns
    return max(72, min(int(columns * 0.9), 126))


def hline(left: str, middle: str, right: str, widths: list[int]) -> str:
    return left + middle.join("─" * width for width in widths) + right


def centered_row_line(
    values: list[str],
    widths: list[int],
    styles: list[str | None] | None = None,
) -> str:
    styles = styles or [None] * len(values)
    cells = []
    for value, width, style in zip(values, widths, styles):
        cells.append(center_tier_cell(value, width) if style == "tier" else center_cell(value, width))
    return "│" + "│".join(cells) + "│"


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


def choose_scope(update_result=None) -> tuple[str, str]:
    update_message_pending = update_result is not None
    while True:
        print_header("mabiDB Rune Search")
        if update_message_pending:
            print_update_result(update_result)
            update_message_pending = False
        print("검색할 그룹을 선택하세요.\n\n초성 검색,영문검색을 지원합니다!\nex)ㅇㄷㅎㅂ > 아득한빛\nex)dkemr > 아득")
        print()
        print("  1. 무기 / 방어구 / 엠블럼 룬")
        print("  2. 장신구 룬")
        print("  3. 생활 채집")
        print()
        choice = input("번호 입력 > ").strip()
        if choice in SCOPES:
            return SCOPES[choice]
        print("1, 2, 3 중 하나를 입력하세요.")
        input("계속하려면 Enter를 누르세요.")


def search_help_text(scope: str) -> str:
    if scope == "gathering":
        return "채집물 이름으로 검색 가능합니다. 예) 양털, 마나석 등\n초성검색도 가능합니다. ex) ㄷㄲㅇㅇㅌ > 두꺼운양털"
    if scope == "accessory":
        return "이름, 클래스, 내용으로 검색 가능합니다. 예) 관통, 기사, 홀리스피어\n초성검색도 가능합니다. ex) ㅅㄹㅂㅋ > 수레바퀴"
    return "이름, 내용, 태그, 줄임말로 검색 가능합니다. 예) 쏟불, 무방비, 주피증\n초성검색도 가능합니다. ex) ㅇㄷㅎㅂ > 아득한빛"


def is_quit_command(text: str) -> bool:
    return text.lower() in QUIT_COMMANDS


def is_back_command(text: str, *, allow_b: bool = False) -> bool:
    commands = BACK_COMMANDS | ({"b"} if allow_b else set())
    return text.lower() in commands


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


def format_result_sections(row, attributes: dict[str, str]) -> list[str]:
    sections = []
    if attributes.get("직업") or attributes.get("스킬 슬롯"):
        class_name = attributes.get("직업", "")
        skill_slot = attributes.get("스킬 슬롯", "")
        prefix = class_name
        if skill_slot:
            prefix = f"{prefix} 스킬 {skill_slot}".strip()
        if prefix:
            sections.append(prefix)
    if row["description"]:
        sections.append(row["description"])
    elif row["summary"]:
        sections.append(row["summary"])
    if attributes.get("태그"):
        sections.append(attributes["태그"])
    return sections


def summary_columns(row, attributes: dict[str, str]) -> list[str]:
    tier = attributes.get("등급", "")
    values = [
        row["name"],
        TYPE_LABELS.get(row["type"], row["type"]),
    ]
    if tier:
        values.append(format_tier(tier))
    else:
        values.append("")
    return values


def summary_widths(width: int) -> list[int]:
    inner_width = width - 4
    first = inner_width // 3
    second = inner_width // 3
    third = inner_width - first - second
    return [first, second, third]


def gathering_card_widths(width: int) -> list[int]:
    inner_width = width - 3
    label = display_width("채집 장소") + 2
    value = inner_width - label
    return [label, value]


def pad_line(line: str, width: int) -> str:
    return line + (" " * max(0, width - display_width(strip_ansi(line))))


def print_wrapped_centered_row(values: list[str], widths: list[int]) -> None:
    wrapped_values = [wrap_text(value, max(1, width - 2)) for value, width in zip(values, widths)]
    row_count = max(len(lines) for lines in wrapped_values)
    for row_index in range(row_count):
        cells = []
        for lines, width in zip(wrapped_values, widths):
            line = lines[row_index] if row_index < len(lines) else ""
            cells.append(center_cell(line, width))
        print("│" + "│".join(cells) + "│")


def print_wrapped_row(values: list[str], widths: list[int], aligns: list[str]) -> None:
    for line in render_wrapped_row(values, widths, aligns):
        print(line)


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
    while len(lines) < height:
        lines.append(blank_wrapped_row(widths))
    return lines


def render_labeled_lines_row_fixed(
    label: str,
    value_lines: list[str],
    widths: list[int],
    *,
    height: int,
) -> list[str]:
    lines = value_lines or [""]
    rendered = []
    for index, line in enumerate(lines):
        label_cell = center_cell(label if index == 0 else "", widths[0])
        value_cell = left_cell(line, widths[1])
        rendered.append("│" + label_cell + "│" + value_cell + "│")
    while len(rendered) < height:
        rendered.append(blank_wrapped_row(widths))
    return rendered


def method_display_lines(method: str) -> list[str]:
    parts = [part.strip() for part in method.split("//") if part.strip()]
    if not parts:
        return []
    return [f"· {part}" for part in parts]


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


def print_full_width_lines(lines: list[str], content_width: int, *, align: str = "center") -> None:
    for line in render_full_width_lines(lines, content_width, align=align):
        print(line)


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
    for part in DESCRIPTION_BREAK_PATTERN.split(value):
        part = part.strip()
        if URL_PATTERN.search(part):
            wrapped.append(part)
            continue
        wrapped.extend(wrap_text(part, max(1, width - display_width(indent))))
    return [indent + line for line in wrapped]


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


def print_gathering_result_box(row, attributes: dict[str, str]) -> None:
    for line in render_gathering_result_card(row, attributes, min(64, terminal_width())):
        print(line)


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


def print_result_box(row, attributes: dict[str, str]) -> None:
    if attributes.get("시트") == "Gathering":
        print_gathering_result_box(row, attributes)
        return

    width = terminal_width()
    content_width = width - 2
    wrap_width = max(20, content_width - 4)
    top_widths = summary_widths(width)
    print(hline("┌", "┬", "┐", top_widths))
    print(centered_row_line(summary_columns(row, attributes), top_widths, [None, None, "tier"]))

    sections = format_result_sections(row, attributes)
    if sections:
        print(hline("├", "┴", "┤", top_widths))
    for section_index, section in enumerate(sections):
        for line in wrap_text(section, wrap_width):
            print("│" + center_cell(line, content_width) + "│")
        if section_index != len(sections) - 1:
            print("├" + ("─" * content_width) + "┤")
    print("└" + ("─" * content_width) + "┘")


def print_update_result(update_result) -> None:
    if update_result is None:
        return
    if update_result.status == "updated":
        print(f"DB 업데이트 완료: {update_result.message}")
        print()
    elif update_result.status == "failed":
        print("DB 업데이트 확인 실패. 기존 DB로 실행합니다.")
        print()


def print_results(conn, keyword: str, scope: str, scope_label: str) -> None:
    rows = search_entries(conn, keyword, 20, scope)

    print_header("mabiDB Rune Search", scope_label)
    print(f"검색어: {keyword}")
    print(f"결과: {len(rows)}건")
    if scope == "gathering":
        print("황금 재료 채집은 황금을 검색해주세요")
    print()

    if not rows:
        print_full_box(["검색 결과가 없습니다."])
        return

    if scope == "gathering":
        print_gathering_result_cards(rows, conn)
        return

    for index, row in enumerate(rows, start=1):
        attributes = attributes_to_dict(get_attributes(conn, row["id"]))
        print_result_box(row, attributes)
        if index != len(rows):
            print()
            print()
            print()


def search_loop(scope: str, scope_label: str) -> None:
    with connect() as conn:
        while True:
            print_header("mabiDB Rune Search", scope_label)
            print(f"{search_help_text(scope)}\n")
            print("Enter:검색  1:뒤로가기  2:종료\n")
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

            while True:
                print_results(conn, keyword, scope, scope_label)
                print("Enter:검색  1:뒤로가기  2:종료\n")
                next_input = input("검색어 > ").strip()
                if is_quit_command(next_input):
                    return
                if is_back_command(next_input, allow_b=True):
                    scope, scope_label = choose_scope()
                    break
                if not next_input:
                    break
                keyword = next_input


def run_tui() -> None:
    configure_console()
    update_result = initialize(update_remote=True)
    scope, scope_label = choose_scope(update_result)
    search_loop(scope, scope_label)
    print()
    print(f"DB: {DB_PATH}")


def main() -> None:
    run_tui()


if __name__ == "__main__":
    main()
