from __future__ import annotations

import sys

from db import connect, initialize


HELP_TEXT = """동의어 편집 명령어

/목록
/추가 크리,치뎀,치확 치명타
/삭제 크리 치명타
/종료
"""


def split_keywords(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def list_synonyms() -> None:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT keyword, expansion
            FROM search_synonyms
            ORDER BY expansion, keyword
            """
        ).fetchall()

    if not rows:
        print("등록된 동의어가 없습니다.")
        return

    current_expansion = None
    for row in rows:
        if row["expansion"] != current_expansion:
            current_expansion = row["expansion"]
            print()
            print(current_expansion)
        print(f"  - {row['keyword']}")


def add_synonyms(keyword_text: str, expansion: str) -> None:
    keywords = split_keywords(keyword_text)
    if not keywords or not expansion:
        print("사용법: /추가 크리,치뎀,치확 치명타")
        return

    with connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO search_synonyms(keyword, expansion)
            VALUES (?, ?)
            """,
            [(keyword, expansion) for keyword in keywords],
        )

    print(f"{expansion}의 유사어 추가: {', '.join(keywords)}")


def delete_synonym(keyword: str, expansion: str) -> None:
    if not keyword or not expansion:
        print("사용법: /삭제 크리 치명타")
        return

    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM search_synonyms
            WHERE keyword = ? AND expansion = ?
            """,
            (keyword, expansion),
        )

    if cursor.rowcount:
        print(f"{expansion}의 유사어 삭제: {keyword}")
    else:
        print(f"삭제할 항목이 없습니다: {keyword} -> {expansion}")


def handle_command(command: str) -> bool:
    command = command.strip()
    if not command:
        return True
    if command in {"/종료", "/quit", "/exit", "q"}:
        return False
    if command in {"/도움말", "/help"}:
        print(HELP_TEXT)
        return True
    if command == "/목록":
        list_synonyms()
        return True

    if command.startswith("/추가 "):
        args = command[len("/추가 ") :].strip().split(maxsplit=1)
        if len(args) != 2:
            print("사용법: /추가 크리,치뎀,치확 치명타")
            return True
        add_synonyms(args[0], args[1].strip())
        return True

    if command.startswith("/삭제 "):
        args = command[len("/삭제 ") :].strip().split(maxsplit=1)
        if len(args) != 2:
            print("사용법: /삭제 크리 치명타")
            return True
        delete_synonym(args[0].strip(), args[1].strip())
        return True

    print("알 수 없는 명령어입니다. /도움말을 입력하세요.")
    return True


def main() -> None:
    initialize()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")

    print(HELP_TEXT)
    while True:
        try:
            command = input("> ")
        except EOFError:
            break
        if not handle_command(command):
            break


if __name__ == "__main__":
    main()
