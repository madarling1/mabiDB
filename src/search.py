from __future__ import annotations

import argparse
import re
import sqlite3

from database import DB_PATH, connect, initialize


ACCESSORY_RUNE_SOURCE = "db.xlsx#AccRune"
ACCESSORY_RUNE_SHEET = "AccRune"
RECIPE_SOURCE = "db.xlsx#Recipe"
DECO_SOURCE = "db.xlsx#Deco"
INGREDIENT_QUANTITY_SUFFIX = re.compile(r"\s*[×xX]\s*\d+\s*$")
CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
JONGSEONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
ENGLISH_TO_JAMO = {
    "r": "ㄱ", "R": "ㄲ", "s": "ㄴ", "e": "ㄷ", "E": "ㄸ", "f": "ㄹ",
    "a": "ㅁ", "q": "ㅂ", "Q": "ㅃ", "t": "ㅅ", "T": "ㅆ", "d": "ㅇ",
    "w": "ㅈ", "W": "ㅉ", "c": "ㅊ", "z": "ㅋ", "x": "ㅌ", "v": "ㅍ", "g": "ㅎ",
    "k": "ㅏ", "o": "ㅐ", "i": "ㅑ", "O": "ㅒ", "j": "ㅓ", "p": "ㅔ",
    "u": "ㅕ", "P": "ㅖ", "h": "ㅗ", "y": "ㅛ", "n": "ㅜ", "b": "ㅠ",
    "m": "ㅡ", "l": "ㅣ",
}
COMPOUND_VOWELS = {
    ("ㅗ", "ㅏ"): "ㅘ",
    ("ㅗ", "ㅐ"): "ㅙ",
    ("ㅗ", "ㅣ"): "ㅚ",
    ("ㅜ", "ㅓ"): "ㅝ",
    ("ㅜ", "ㅔ"): "ㅞ",
    ("ㅜ", "ㅣ"): "ㅟ",
    ("ㅡ", "ㅣ"): "ㅢ",
}
COMPOUND_FINALS = {
    ("ㄱ", "ㅅ"): "ㄳ",
    ("ㄴ", "ㅈ"): "ㄵ",
    ("ㄴ", "ㅎ"): "ㄶ",
    ("ㄹ", "ㄱ"): "ㄺ",
    ("ㄹ", "ㅁ"): "ㄻ",
    ("ㄹ", "ㅂ"): "ㄼ",
    ("ㄹ", "ㅅ"): "ㄽ",
    ("ㄹ", "ㅌ"): "ㄾ",
    ("ㄹ", "ㅍ"): "ㄿ",
    ("ㄹ", "ㅎ"): "ㅀ",
    ("ㅂ", "ㅅ"): "ㅄ",
}
LEADING_TO_FINAL = {
    "ㄱ": "ㄱ", "ㄲ": "ㄲ", "ㄴ": "ㄴ", "ㄷ": "ㄷ", "ㄹ": "ㄹ", "ㅁ": "ㅁ",
    "ㅂ": "ㅂ", "ㅅ": "ㅅ", "ㅆ": "ㅆ", "ㅇ": "ㅇ", "ㅈ": "ㅈ", "ㅊ": "ㅊ",
    "ㅋ": "ㅋ", "ㅌ": "ㅌ", "ㅍ": "ㅍ", "ㅎ": "ㅎ",
}
FINAL_TO_LEADING = {value: key for key, value in LEADING_TO_FINAL.items()}
CHOSEONG_SET = set(CHOSEONG)
VOWEL_SET = set(JUNGSEONG)
CONSONANT_SET = set(LEADING_TO_FINAL)


def compose_hangul_syllable(initial: str, vowel: str, final: str = "") -> str:
    return chr(
        0xAC00
        + (CHOSEONG.index(initial) * 21 + JUNGSEONG.index(vowel)) * 28
        + JONGSEONG.index(final)
    )


def english_to_korean(text: str) -> str:
    jamo = [ENGLISH_TO_JAMO.get(char, char) for char in text]
    result = []
    index = 0
    while index < len(jamo):
        current = jamo[index]
        if current not in CONSONANT_SET or index + 1 >= len(jamo) or jamo[index + 1] not in VOWEL_SET:
            result.append(current)
            index += 1
            continue

        initial = current
        vowel = jamo[index + 1]
        index += 2
        if index < len(jamo) and (vowel, jamo[index]) in COMPOUND_VOWELS:
            vowel = COMPOUND_VOWELS[(vowel, jamo[index])]
            index += 1

        final = ""
        if index < len(jamo) and jamo[index] in CONSONANT_SET:
            first_final = LEADING_TO_FINAL[jamo[index]]
            if index + 1 < len(jamo) and jamo[index + 1] in VOWEL_SET:
                final = ""
            elif (
                index + 1 < len(jamo)
                and jamo[index + 1] in CONSONANT_SET
                and (first_final, LEADING_TO_FINAL[jamo[index + 1]]) in COMPOUND_FINALS
                and not (index + 2 < len(jamo) and jamo[index + 2] in VOWEL_SET)
            ):
                final = COMPOUND_FINALS[(first_final, LEADING_TO_FINAL[jamo[index + 1]])]
                index += 2
            else:
                final = first_final
                index += 1

        result.append(compose_hangul_syllable(initial, vowel, final))

    return "".join(result)


def hangul_initials(text: str) -> str:
    result = []
    for char in text:
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            result.append(CHOSEONG[(code - 0xAC00) // 588])
        elif char in CHOSEONG_SET:
            result.append(char)
    return "".join(result)


def is_initial_search(text: str) -> bool:
    compact = "".join(text.split())
    return bool(compact) and all(char in CHOSEONG_SET for char in compact)


def is_hangul_char(char: str) -> bool:
    code = ord(char)
    return 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F


def should_convert_english_to_korean(text: str) -> bool:
    return not any(is_hangul_char(char) for char in text)


def fold_search_text(text: object) -> str:
    return str(text).casefold()


def compact_search_text(text: object) -> str:
    return "".join(fold_search_text(text).split())


def initial_search_matches(text: str, keyword: str) -> bool:
    initial_keyword = "".join(keyword.split())
    if not initial_keyword:
        return False

    initials = hangul_initials(text)
    if initial_keyword in initials:
        return True

    return any(hangul_initials(word).startswith(initial_keyword) for word in text.split())


def search_variants(keyword: str) -> list[str]:
    stripped = keyword.strip()
    converted = normalize_search_keyword(stripped)
    return list(dict.fromkeys(term for term in (converted, stripped) if term))


def expand_search_terms(conn: sqlite3.Connection, keyword: str) -> list[str]:
    base_keyword = normalize_search_keyword(keyword)
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
    terms = [*search_variants(keyword), *[row["expansion"] for row in rows]]
    return list(dict.fromkeys(term for term in terms if term))


def normalize_search_keyword(keyword: str) -> str:
    stripped = keyword.strip()
    return english_to_korean(stripped) if should_convert_english_to_korean(stripped) else stripped


def find_accessory_classes(conn: sqlite3.Connection, terms: list[str]) -> list[str]:
    classes: list[str] = []
    seen: set[str] = set()
    for term in terms:
        compact_term = "".join(term.split())
        rows = conn.execute(
            """
            SELECT DISTINCT class_name
            FROM rune_details
            WHERE source_sheet = ?
              AND (
                class_name COLLATE NOCASE = ?
                OR REPLACE(class_name, ' ', '') COLLATE NOCASE = ?
              )
            ORDER BY class_name
            """,
            (ACCESSORY_RUNE_SHEET, term, compact_term),
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
        WHERE e.source = ?
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
        (ACCESSORY_RUNE_SOURCE, *class_names, limit),
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
            WHERE e.name COLLATE NOCASE = ?

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
                OR (? = 'equipment' AND e.source = 'db.xlsx#Runes')
                OR (? = 'accessory' AND e.source = 'db.xlsx#AccRune')
                OR (? = 'barter' AND e.source = 'db.xlsx#Barter')
                OR (? = 'recipe' AND e.source = 'db.xlsx#Recipe')
                OR (? = 'deco' AND e.source = 'db.xlsx#Deco')
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
            scope,
            scope,
            scope,
            limit,
        ),
    ).fetchall()


def search_gathering_entries_by_name(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int = 10,
) -> list[sqlite3.Row]:
    keyword = keyword.strip()
    if not keyword:
        return []

    variants = search_variants(keyword)
    initial_keyword = normalize_search_keyword(keyword)
    if is_initial_search(initial_keyword):
        rows = conn.execute(
            """
            SELECT
                e.id,
                e.type,
                e.name,
                e.summary,
                e.description,
                80 AS score,
                COALESCE(rd.class_name, '') AS class_name,
                rd.skill_slot AS skill_slot,
                COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
            FROM entries e
            LEFT JOIN rune_details rd ON rd.entry_id = e.id
            LEFT JOIN entry_tags et ON et.entry_id = e.id
            LEFT JOIN tags t ON t.id = et.tag_id
            WHERE e.source = 'db.xlsx#Gathering'
            GROUP BY
                e.id,
                e.type,
                e.name,
                e.summary,
                e.description,
                rd.class_name,
                rd.skill_slot
            ORDER BY e.id ASC
            """
        ).fetchall()
        return [row for row in rows if initial_search_matches(row["name"], initial_keyword)][:limit]

    clauses = []
    params: list[str | int] = []
    for variant in variants:
        term = f"%{variant}%"
        compact_variant = "".join(variant.split())
        compact_term = f"%{compact_variant}%"
        clauses.extend(
            [
                """
                SELECT e.id, 100 AS score
                FROM entries e
                WHERE e.source = 'db.xlsx#Gathering'
                  AND e.name COLLATE NOCASE = ?
                """,
                """
                SELECT e.id, 80 AS score
                FROM entries e
                WHERE e.source = 'db.xlsx#Gathering'
                  AND e.name LIKE ?
                """,
                """
                SELECT e.id, 75 AS score
                FROM entries e
                WHERE e.source = 'db.xlsx#Gathering'
                  AND REPLACE(e.name, ' ', '') LIKE ?
                """,
                """
                SELECT a.entry_id AS id, 70 AS score
                FROM aliases a
                JOIN entries e ON e.id = a.entry_id
                WHERE e.source = 'db.xlsx#Gathering'
                  AND a.alias LIKE ?
                """,
                """
                SELECT a.entry_id AS id, 65 AS score
                FROM aliases a
                JOIN entries e ON e.id = a.entry_id
                WHERE e.source = 'db.xlsx#Gathering'
                  AND REPLACE(a.alias, ' ', '') LIKE ?
                """,
            ]
        )
        params.extend([variant, term, compact_term, term, compact_term])

    matched_sql = "\nUNION ALL\n".join(clauses)
    return conn.execute(
        f"""
        WITH matched AS (
            {matched_sql}
        ),
        ranked AS (
            SELECT id, MAX(score) AS score
            FROM matched
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
        ORDER BY r.score DESC, e.id ASC
        """,
        (*params, limit),
    ).fetchall()


def fetch_craft_entries(conn: sqlite3.Connection, source: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            0 AS score,
            COALESCE(rd.class_name, '') AS class_name,
            rd.skill_slot AS skill_slot,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
        FROM entries e
        LEFT JOIN rune_details rd ON rd.entry_id = e.id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        WHERE e.source = ?
        GROUP BY
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            rd.class_name,
            rd.skill_slot
        ORDER BY e.id ASC
        """,
        (source,),
    ).fetchall()


def fetch_recipe_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return fetch_craft_entries(conn, RECIPE_SOURCE)


def fetch_deco_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return fetch_craft_entries(conn, DECO_SOURCE)


def search_craft_entries_by_name(
    conn: sqlite3.Connection,
    keyword: str,
    source: str,
    limit: int | None = 20,
) -> list[sqlite3.Row]:
    keyword = keyword.strip()
    if not keyword:
        return []

    initial_keyword = normalize_search_keyword(keyword)
    if is_initial_search(initial_keyword):
        rows = fetch_craft_entries(conn, source)
        matched_rows = [row for row in rows if initial_search_matches(row["name"], initial_keyword)]
        return matched_rows if limit is None else matched_rows[:limit]

    terms = expand_search_terms(conn, keyword)
    if not terms:
        return []

    clauses = []
    params: list[str | int] = []
    for term in terms:
        like_term = f"%{term}%"
        compact_term = f"%{''.join(term.split())}%"
        clauses.extend(
            [
                """
                SELECT e.id, 100 AS score
                FROM entries e
                WHERE e.source = ?
                  AND e.name COLLATE NOCASE = ?
                """,
                """
                SELECT e.id, 80 AS score
                FROM entries e
                WHERE e.source = ?
                  AND e.name LIKE ?
                """,
                """
                SELECT e.id, 75 AS score
                FROM entries e
                WHERE e.source = ?
                  AND REPLACE(e.name, ' ', '') LIKE ?
                """,
                """
                SELECT a.entry_id AS id, 70 AS score
                FROM aliases a
                JOIN entries e ON e.id = a.entry_id
                WHERE e.source = ?
                  AND a.alias LIKE ?
                """,
                """
                SELECT a.entry_id AS id, 65 AS score
                FROM aliases a
                JOIN entries e ON e.id = a.entry_id
                WHERE e.source = ?
                  AND REPLACE(a.alias, ' ', '') LIKE ?
                """,
            ]
        )
        params.extend(
            [
                source,
                term,
                source,
                like_term,
                source,
                compact_term,
                source,
                like_term,
                source,
                compact_term,
            ]
        )

    matched_sql = "\nUNION ALL\n".join(clauses)
    limit_sql = "" if limit is None else "LIMIT ?"
    limit_params = () if limit is None else (limit,)
    return conn.execute(
        f"""
        WITH matched AS (
            {matched_sql}
        ),
        ranked AS (
            SELECT id, MAX(score) AS score
            FROM matched
            GROUP BY id
            ORDER BY score DESC, id ASC
            {limit_sql}
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
        (*params, *limit_params),
    ).fetchall()


def search_recipe_entries_by_name(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int | None = 20,
) -> list[sqlite3.Row]:
    return search_craft_entries_by_name(conn, keyword, RECIPE_SOURCE, limit)


def search_deco_entries_by_name(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int | None = 20,
) -> list[sqlite3.Row]:
    return search_craft_entries_by_name(conn, keyword, DECO_SOURCE, limit)


def recipe_ingredient_parts(recipe: str) -> list[str]:
    return [part.strip() for part in recipe.split("//") if part.strip()]


def recipe_ingredient_name(value: str) -> str:
    return INGREDIENT_QUANTITY_SUFFIX.sub("", value).strip()


def expand_ingredient_search_terms(terms: list[str]) -> list[str]:
    expanded: list[str] = []
    for term in terms:
        for value in (term, recipe_ingredient_name(term)):
            if value and value not in expanded:
                expanded.append(value)
    return expanded


def recipe_ingredient_match_score(value: str, terms: list[str], initial_keyword: str) -> int:
    if not value:
        return 0
    if is_initial_search(initial_keyword):
        return 1 if initial_search_matches(value, initial_keyword) else 0

    ingredient_name = recipe_ingredient_name(value)
    folded_name = fold_search_text(ingredient_name)
    folded_terms = [fold_search_text(term) for term in terms]
    compact_name = compact_search_text(ingredient_name)
    compact_terms = [compact_search_text(term) for term in terms]
    if any(term == folded_name for term in folded_terms) or any(
        compact_term == compact_name
        for compact_term in compact_terms
    ):
        return 2
    if any(term in folded_name for term in folded_terms) or any(
        compact_term in compact_name
        for compact_term in compact_terms
    ):
        return 1
    return 0


def search_craft_entries_by_ingredient(
    conn: sqlite3.Connection,
    keyword: str,
    source: str,
    limit: int | None = 50,
) -> list[sqlite3.Row]:
    initial_keyword = normalize_search_keyword(keyword)
    terms = [initial_keyword] if is_initial_search(initial_keyword) else expand_search_terms(conn, keyword)
    if not is_initial_search(initial_keyword):
        terms = expand_ingredient_search_terms(terms)
    if not terms:
        return []

    exact_matches: list[sqlite3.Row] = []
    partial_matches: list[sqlite3.Row] = []
    for row in fetch_craft_entries(conn, source):
        score = max(
            [
                recipe_ingredient_match_score(part, terms, initial_keyword)
                for part in recipe_ingredient_parts(row["description"])
            ],
            default=0,
        )
        if score == 2:
            exact_matches.append(row)
        elif score == 1:
            partial_matches.append(row)

    matched = exact_matches or partial_matches
    rows = sorted(matched, key=lambda row: row["name"])
    return rows if limit is None else rows[:limit]


def search_recipe_entries_by_ingredient(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int | None = 50,
) -> list[sqlite3.Row]:
    return search_craft_entries_by_ingredient(conn, keyword, RECIPE_SOURCE, limit)


def search_deco_entries_by_ingredient(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int | None = 50,
) -> list[sqlite3.Row]:
    return search_craft_entries_by_ingredient(conn, keyword, DECO_SOURCE, limit)


def search_entries_by_initials(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int = 10,
    rune_scope: str | None = None,
) -> list[sqlite3.Row]:
    scope = rune_scope or ""
    rows = conn.execute(
        """
        SELECT
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            80 AS score,
            COALESCE(rd.class_name, '') AS class_name,
            rd.skill_slot AS skill_slot,
            COALESCE(GROUP_CONCAT(DISTINCT t.name), '') AS tags
        FROM entries e
        LEFT JOIN rune_details rd ON rd.entry_id = e.id
        LEFT JOIN entry_tags et ON et.entry_id = e.id
        LEFT JOIN tags t ON t.id = et.tag_id
        WHERE
            ? = ''
            OR (? = 'equipment' AND e.source = 'db.xlsx#Runes')
            OR (? = 'accessory' AND e.source = 'db.xlsx#AccRune')
            OR (? = 'gathering' AND e.source = 'db.xlsx#Gathering')
            OR (? = 'barter' AND e.source = 'db.xlsx#Barter')
            OR (? = 'recipe' AND e.source = 'db.xlsx#Recipe')
            OR (? = 'deco' AND e.source = 'db.xlsx#Deco')
        GROUP BY
            e.id,
            e.type,
            e.name,
            e.summary,
            e.description,
            rd.class_name,
            rd.skill_slot
        ORDER BY e.id ASC
        """,
        (scope, scope, scope, scope, scope, scope, scope),
    ).fetchall()
    return [row for row in rows if initial_search_matches(row["name"], keyword)][:limit]


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
    if rune_scope == "recipe":
        direct_rows = search_recipe_entries_by_name(conn, keyword, limit)
        direct_ids = {row["id"] for row in direct_rows}
        usage_rows = [
            row
            for row in search_recipe_entries_by_ingredient(conn, keyword, max(limit, 50))
            if row["id"] not in direct_ids
        ]
        return [*direct_rows, *usage_rows][:limit]
    if rune_scope == "deco":
        direct_rows = search_deco_entries_by_name(conn, keyword, limit)
        direct_ids = {row["id"] for row in direct_rows}
        usage_rows = [
            row
            for row in search_deco_entries_by_ingredient(conn, keyword, max(limit, 50))
            if row["id"] not in direct_ids
        ]
        return [*direct_rows, *usage_rows][:limit]

    initial_keyword = normalize_search_keyword(keyword)
    if is_initial_search(initial_keyword):
        return search_entries_by_initials(conn, initial_keyword, limit, rune_scope)

    terms = expand_search_terms(conn, keyword)
    if not terms:
        return []

    if rune_scope == "gathering":
        merged: dict[int, sqlite3.Row] = {}
        for term in terms:
            for row in search_gathering_entries_by_name(conn, term, limit):
                current = merged.get(row["id"])
                if current is None or row["score"] > current["score"]:
                    merged[row["id"]] = row
        return sort_search_rows(list(merged.values()), rune_scope)[:limit]

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


def print_update_result(update_result) -> None:
    if update_result is None:
        return
    if update_result.status == "updated":
        print(f"DB 업데이트 완료: {update_result.message}")
    elif update_result.status == "failed":
        print("DB 업데이트 확인 실패. 기존 DB로 실행합니다.")


def run_interactive(limit: int = 10) -> None:
    update_result = initialize(update_remote=True)
    with connect() as conn:
        print_update_result(update_result)
        print("검색어를 입력하세요. 종료하려면 빈 줄, q, quit, exit 중 하나를 입력하세요.")
        while True:
            keyword = input("> ").strip()
            if keyword.lower() in {"", "q", "quit", "exit"}:
                break
            print_search_results(conn, keyword, limit)
    print(f"DB: {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Search mabiDB entries.")
    parser.add_argument("keyword", nargs="?", help="Keyword to search")
    parser.add_argument("--limit", type=int, default=10, help="Maximum result count")
    parser.add_argument(
        "--scope",
        choices=["equipment", "accessory", "gathering", "barter", "recipe", "deco"],
        help="Search group filter: equipment, accessory, gathering, barter, recipe, or deco",
    )
    args = parser.parse_args()

    if not args.keyword:
        run_interactive(args.limit)
        return

    update_result = initialize(update_remote=True)
    print_update_result(update_result)
    with connect() as conn:
        print_search_results(conn, args.keyword, args.limit, args.scope)
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
