# MobiDB

Mabinogi 룬 정보를 SQLite DB로 저장하고 터미널 UI에서 검색하는 도구입니다.

## 주요 파일

```text
db.xlsx               원본 엑셀 데이터
resources/schema.sql  SQLite 테이블 구조
data/mobidb.sqlite    생성된 DB 파일
config/remote_db.json GitHub DB 업데이트 설정

src/db.py             DB 연결/초기화 공통 코드
src/import_excel.py   db.xlsx -> SQLite import
src/search.py         검색 로직
src/tui.py            터미널 검색 UI 실행 파일
src/synonyms.py       검색 동의어 편집기
```

## 실행

검색기 실행:

```powershell
python .\src\tui.py
```

위 명령은 새 PowerShell 창을 열고 검색 UI를 실행합니다.

## GitHub DB 자동 업데이트

`RemoteDB` 버전은 검색기 실행 시 GitHub의 SQLite 파일을 확인하고, 새 버전이면 `data/mobidb.sqlite`를 내려받아 교체합니다.

사용 방법:

1. `config/remote_db.example.json`을 `config/remote_db.json`으로 복사
2. `database_url`, `version_url`을 본인 GitHub raw URL로 변경
3. GitHub에 `data/mobidb.sqlite`를 다시 올릴 때마다 `data/db_version.txt` 값도 함께 변경

예:

```json
{
  "database_url": "https://raw.githubusercontent.com/USER/REPO/main/data/mobidb.sqlite",
  "version_url": "https://raw.githubusercontent.com/USER/REPO/main/data/db_version.txt",
  "timeout_seconds": 15
}
```

`config/remote_db.json`이 없거나 `database_url`이 비어 있으면 기존처럼 로컬 `data/mobidb.sqlite`만 사용합니다.

## exe 빌드

PyInstaller가 설치된 환경에서:

```powershell
.\build.ps1
```

완성된 배포 폴더:

```text
dist/MobiDB.zip

dist/MobiDB/
  MobiDB.exe
  _internal/
  USER_GUIDE.txt
  config/remote_db.json
  data/mobidb.sqlite
```

배포할 때는 `dist/MobiDB.zip`을 GitHub Release에 올리면 됩니다. `remote_db.json`이 있으면 빌드 결과의 `config/` 폴더에 함께 포함됩니다. 없으면 사용자 쪽에서 압축을 풀고 `config/remote_db.example.json`을 `config/remote_db.json`으로 이름 바꾼 뒤 GitHub raw URL을 입력하면 됩니다.

엑셀 데이터를 DB에 다시 반영:

```powershell
python .\src\import_excel.py --file .\db.xlsx
```

동의어 편집:

```powershell
python .\src\synonyms.py
```

예:

```text
/추가 크리,치뎀,치확 치명타
/삭제 크리 치명타
/목록
```
