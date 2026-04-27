from __future__ import annotations

import argparse
import sqlite3

from db import DB_PATH, connect, initialize


EQUIPMENT_RUNE_TYPES = ("WeaponRune", "ArmorRune", "EmblemRune")
ACCESSORY_RUNE_TYPE = "AccessoryRune"


def expand_search_terms(conn: sqlite3.Connection, keyword: str) -> list[str]:
    base_keyword = keyword.strip()
    if not base_keyword:
        return []

    rows = conn.execute(
        """
        SELECT expansion
        FROM search_synonyms
        WHERE keyword = ?
        ORDER BY expansion
        """,
        (base_keyword,),
    ).fetchall()
    terms = [base_keyword, *[row["expansion"] for row in rows]]
    return list(dict.fromkeys(term for term in terms if term))


def find_accessory_classes(conn: sqlite3.Connection, terms: list[str]) -> list[str]:
    classes: list[str] = []
    seen: set[str] = set()
    for term in terms:
        compact_term = "".join(term.split())
        rows = conn.execute(
            """
            SELECT DISTINCT class_name
            FROM rune_details
            WHERE rune_kind = ?
              AND (class_name = ? OR REPLACE(class_name, ' ', '') = ?)
            ORDER BY class_name
            """,
            (ACCESSORY_RUNE_TYPE, term, compact_term),
        ).fetchall()
        for row in rows:
            class_name = row["class_name"]
            if class_name not in seen:
                seen.add(class_name)
                classes.append(class_name)
    return classes


def search_accessory_classes(
    conn: sqlite3.Connection,
    class_names: list[str],
    limit: int = 10,
) -> list[sqlite3.Row]:
    if not class_names:
        return []

    placeholders = ", ".join("?" for _ in class_names)
    return conn.execute(
        f"""
        SELECT
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            100 AS score,
            COALESCE(rd.class_name, '') AS class_name,
            rd.skill_slot AS skill_slot,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
        FROM entries e
        JOIN rune_details rd ON rd.entry_id = e.id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        WHERE e.type = ?
          AND rd.class_name IN ({placeholders})
        GROUP BY
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            rd.class_name,
            rd.skill_slot
        ORDER BY rd.class_name ASC, rd.skill_slot ASC, e.name ASC
        LIMIT ?
        """,
        (ACCESSORY_RUNE_TYPE, *class_names, limit),
    ).fetchall()


def search_entries_for_term(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int = 10,
    rune_scope: str | None = None,
) -> list[sqlite3.Row]:
    keyword = keyword.strip()
    term = f"%{keyword}%"
    compact_keyword = "".join(keyword.split())
    compact_term = f"%{compact_keyword}%"
    if term == "%%":
        return []

    scope = rune_scope or ""
    return conn.execute(
        """
        WITH matched AS (
            SELECT e.id, 100 AS score
            FROM entries e
            WHERE e.name = ?

            UNION ALL

            SELECT e.id, 80 AS score
            FROM entries e
            WHERE e.name LIKE ?

            UNION ALL

            SELECT e.id, 75 AS score
            FROM entries e
            WHERE REPLACE(e.name, ' ', '') LIKE ?

            UNION ALL

            SELECT a.entry_id AS id, 70 AS score
            FROM aliases a
            WHERE a.alias LIKE ?

            UNION ALL

            SELECT a.entry_id AS id, 65 AS score
            FROM aliases a
            WHERE REPLACE(a.alias, ' ', '') LIKE ?

            UNION ALL

            SELECT et.entry_id AS id, 50 AS score
            FROM entry_tags et
            JOIN tags t ON t.id = et.tag_id
            WHERE t.name LIKE ?

            UNION ALL

            SELECT et.entry_id AS id, 45 AS score
            FROM entry_tags et
            JOIN tags t ON t.id = et.tag_id
            WHERE REPLACE(t.name, ' ', '') LIKE ?

            UNION ALL

            SELECT e.id, 30 AS score
            FROM entries e
            WHERE e.summary LIKE ? OR e.description LIKE ?

            UNION ALL

            SELECT e.id, 25 AS score
            FROM entries e
            WHERE REPLACE(e.summary, ' ', '') LIKE ? OR REPLACE(e.description, ' ', '') LIKE ?

            UNION ALL

            SELECT attr.entry_id AS id, 20 AS score
            FROM attributes attr
            WHERE attr.key LIKE ? OR attr.value LIKE ?

            UNION ALL

            SELECT attr.entry_id AS id, 15 AS score
            FROM attributes attr
            WHERE REPLACE(attr.key, ' ', '') LIKE ? OR REPLACE(attr.value, ' ', '') LIKE ?
        ),
        scoped AS (
            SELECT m.id, m.score
            FROM matched m
            JOIN entries e ON e.id = m.id
            WHERE
                ? = ''
                OR (? = 'equipment' AND e.type IN ('WeaponRune', 'ArmorRune', 'EmblemRune'))
                OR (? = 'accessory' AND e.type = 'AccessoryRune')
        ),
        ranked AS (
            SELECT id, MAX(score) AS score
            FROM scoped
            GROUP BY id
            ORDER BY score DESC, id ASC
            LIMIT ?
        )
        SELECT
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            r.score,
            COALESCE(rd.class_name, '') AS class_name,
            rd.skill_slot AS skill_slot,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
        FROM ranked r
        JOIN entries e ON e.id = r.id
        LEFT JOIN rune_details rd ON rd.entry_id = e.id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        GROUP BY
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            r.score,
            rd.class_name,
            rd.skill_slot
        ORDER BY r.score DESC, e.name ASC
        """,
        (
            keyword,
            term,
            compact_term,
            term,
            compact_term,
            term,
            compact_term,
            term,
            term,
            compact_term,
            compact_term,
            term,
            term,
            compact_term,
            compact_term,
            scope,
            scope,
            scope,
            limit,
        ),
    ).fetchall()


def sort_search_rows(rows: list[sqlite3.Row], rune_scope: str | None = None) -> list[sqlite3.Row]:
    if rune_scope != "accessory":
        return sorted(rows, key=lambda row: (-row["score"], row["name"]))

    class_scores: dict[str, int] = {}
    for row in rows:
        class_name = row["class_name"] or ""
        class_scores[class_name] = max(class_scores.get(class_name, 0), row["score"])

    def accessory_key(row: sqlite3.Row) -> tuple[object, ...]:
        class_name = row["class_name"] or ""
        skill_slot = row["skill_slot"] if row["skill_slot"] is not None else 999
        return (
            -class_scores[class_name],
            class_name,
            skill_slot,
            -row["score"],
            row["name"],
        )

    return sorted(rows, key=accessory_key)


def search_entries(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int = 10,
    rune_scope: str | None = None,
) -> list[sqlite3.Row]:
    terms = expand_search_terms(conn, keyword)
    if not terms:
        return []

    if rune_scope == "accessory":
        class_names = find_accessory_classes(conn, terms)
        if class_names:
            rows = search_accessory_classes(conn, class_names, max(limit, 500))
            return sort_search_rows(rows, rune_scope)[:limit]

    merged: dict[int, sqlite3.Row] = {}
    per_term_limit = max(limit, 500) if rune_scope == "accessory" else limit
    for term in terms:
        for row in search_entries_for_term(conn, term, per_term_limit, rune_scope):
            current = merged.get(row["id"])
            if current is None or row["score"] > current["score"]:
                merged[row["id"]] = row

    return sort_search_rows(list(merged.values()), rune_scope)[:limit]


def get_attributes(conn: sqlite3.Connection, entry_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT key, value FROM attributes WHERE entry_id = ? ORDER BY key",
        (entry_id,),
    ).fetchall()


def print_search_results(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int = 10,
    rune_scope: str | None = None,
) -> None:
    rows = search_entries(conn, keyword, limit, rune_scope)
    if not rows:
        print("No results")
        return

    for index, row in enumerate(rows, start=1):
        print(f"{index}. [{row['type']}] {row['name']} (score: {row['score']})")
        if row["summary"]:
            print(f"   {row['summary']}")
        if row["tags"]:
            print(f"   tags: {row['tags']}")

        attributes = get_attributes(conn, row["id"])
        for attribute in attributes:
            print(f"   - {attribute['key']}: {attribute['value']}")
        print()


def run_interactive(limit: int = 10) -> None:
    initialize(update_remote=True)
    with connect() as conn:
        print("검색어를 입력하세요. 종료하려면 빈 줄, q, quit, exit 중 하나를 입력하세요.")
        while True:
            keyword = input("> ").strip()
            if keyword.lower() in {"", "q", "quit", "exit"}:
                break
            print_search_results(conn, keyword, limit)
    print(f"DB: {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search MobiDB entries.")
    parser.add_argument("keyword", nargs="?", help="Keyword to search")
    parser.add_argument("--limit", type=int, default=10, help="Maximum result count")
    parser.add_argument(
        "--scope",
        choices=["equipment", "accessory"],
        help="Rune group filter: equipment or accessory",
    )
    args = parser.parse_args()

    if not args.keyword:
        run_interactive(args.limit)
        return

    initialize(update_remote=True)
    with connect() as conn:
        print_search_results(conn, args.keyword, args.limit, args.scope)
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
