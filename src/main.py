from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from pathlib import Path

from database import DB_PATH, connect, initialize
from search import get_attributes, search_entries


SCOPES = {
    "1": ("equipment", "무기 / 방어구 / 엠블럼"),
    "2": ("accessory", "장신구"),
}

TYPE_LABELS = {
    "WeaponRune": "무기",
    "ArmorRune": "방어구",
    "EmblemRune": "엠블럼",
    "AccessoryRune": "장신구",
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
TIER_STYLES = {
    "전설": "\033[91m",
    "신화": "\033[93m",
    "엘리트": "\033[38;5;135m",
    "에픽": "\033[38;5;199m",
}
QUIT_COMMANDS = {"2", "/q", "/quit", "q", "quit", "exit"}
BACK_COMMANDS = {"1", "/back", "back"}


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
    value = fit_cell(text, width).rstrip()
    padding = max(0, width - display_width(value))
    left = padding // 2
    right = padding - left
    return (" " * left) + value + (" " * right)


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


def choose_scope() -> tuple[str, str]:
    while True:
        print_header("mabiDB Rune Search")
        print("검색할 룬 그룹을 선택하세요.")
        print()
        print("  1. 무기 / 방어구 / 엠블럼 룬")
        print("  2. 장신구 룬")
        print()
        choice = input("번호 입력 > ").strip()
        if choice in SCOPES:
            return SCOPES[choice]
        print("1 또는 2를 입력하세요.")
        input("계속하려면 Enter를 누르세요.")


def search_help_text(scope: str) -> str:
    if scope == "accessory":
        return "이름, 클래스, 내용으로 검색 가능합니다. 예) 관통, 기사, 홀리스피어"
    return "이름, 내용, 태그, 줄임말로 검색 가능합니다. 예) 쏟불, 무방비, 주피증"


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


def print_result_box(row, attributes: dict[str, str]) -> None:
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
    print()

    if not rows:
        print_full_box(["검색 결과가 없습니다."])
        return

    for index, row in enumerate(rows, start=1):
        attributes = attributes_to_dict(get_attributes(conn, row["id"]))
        print_result_box(row, attributes)
        if index != len(rows):
            print()
            print()
            print()


def search_loop(scope: str, scope_label: str) -> None:
    update_result = initialize(update_remote=True)
    with connect() as conn:
        update_message_pending = True
        while True:
            print_header("mabiDB Rune Search", scope_label)
            if update_message_pending:
                print_update_result(update_result)
                update_message_pending = False
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
    scope, scope_label = choose_scope()
    search_loop(scope, scope_label)
    print()
    print(f"DB: {DB_PATH}")


def main() -> None:
    run_tui()


if __name__ == "__main__":
    main()
