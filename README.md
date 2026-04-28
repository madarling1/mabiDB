# mabiDB

마비노기 모바일 룬 정보를 검색하는 터미널 프로그램입니다.

## 다운로드

프로그램은 GitHub **Release**에서 받으면 됩니다.

1. 이 저장소 오른쪽의 **[Releases](../../releases)**를 엽니다.
2. 최신 버전의 `mabiDB.exe`를 다운로드합니다.
3. 다운로드한 `mabiDB.exe`를 실행합니다.

실행 후 DB 파일은 `%LOCALAPPDATA%\mabiDB` 경로에 저장됩니다.
현재 적용된 DB 버전은 %LOCALAPPDATA%\mabiDB\data\db_version.txt 에 표시됩니다.

## 사용 방법

실행하면 검색 범위를 먼저 선택합니다.

```text
숫자 키를 입력 후 엔터!
1. 무기 / 방어구 / 엠블럼 룬
2. 장신구 룬
```

검색어를 입력하면 관련 룬 목록이 표시됩니다.
자주 쓰는 용어, 줄인말, 태그 등으로 검색할 수 있습니다

```text
example
싹쓸바람 : 싹쓸, 싹슬
쏟아진 불길 : 쏟불
갈라진 땅 : 갈땅
홀리 스피어 : 홀스
적에게 주는 피해 증가 : 피증
공격력 증가 : 공증 . . .
태그 : 치명타, 강타, 결함, 피증 등등 
example
싹쓸바람 : 싹쓸, 싹슬
쏟아진 불길 : 쏟불
갈라진 땅 : 갈땅
홀리 스피어 : 홀스
적에게 주는 피해 증가 : 피증
공격력 증가 : 공증 . . .
태그 : 치명타, 강타, 결함, 피증 등등 
```

## DB 업데이트

프로그램을 실행하면 자동으로 최신 룬 DB가 있는지 확인합니다.

새 DB가 있으면 GitHub에서 내려받아 `%LOCALAPPDATA%\mabiDB\data\mobidb.sqlite`를 교체합니다.
사용자가 직접 DB 파일을 수정하거나 업데이트할 필요는 없습니다.

룬 DB는 신규 패치가 있을 경우 제작자가 주기적으로 업데이트합니다.

잘못된 정보 / 변경점 요청은 [여기](https://github.com/madarling1/mabiDB/issues/new)에 적어주세요!

## 저장소 구조

```text
mabiDB/
  README.md
  USER_GUIDE.txt
  build.ps1         # dist/mabiDB.exe 단일 실행 파일을 생성합니다.
  config/           # 원격 DB와 버전 파일 URL을 설정합니다.
    remote_db.json
  data/
    db_version.txt  # 기본 DB 버전이며, 사용자 앱의 업데이트 판단 기준입니다.
    mobidb.sqlite   # exe에 포함되는 기본 룬 DB입니다.
  resources/
    schema.sql      # DB 테이블 구조를 정의합니다.
  src/              # 앱 실행, 검색, DB 초기화, 원격 업데이트 코드를 담습니다.
    main.py
    paths.py
    database.py
    updater.py
    search.py
```