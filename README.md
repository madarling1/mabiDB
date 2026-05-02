# mabiDB

마비노기 모바일 정보를 검색하는 터미널 프로그램입니다.

## 다운로드

프로그램은 GitHub **Release**에서 받으면 됩니다.

1. 이 저장소 오른쪽의 [Releases](../../releases)를 엽니다.
2. 최신 버전의 `mabiDB.exe`를 다운로드합니다.
3. 다운로드한 `mabiDB.exe`를 실행합니다.

실행 후 DB 파일은 `%LOCALAPPDATA%\mabiDB` 경로에 저장됩니다.
현재 적용된 DB 버전은 `%LOCALAPPDATA%\mabiDB\data\db_version.txt` 에 표시됩니다.

## 사용 방법

실행하면 검색 범위를 먼저 선택합니다.

```text
숫자 키를 입력 후 엔터
1 : 무기 / 방어구 / 엠블럼 룬
2 : 장신구 룬
3 : 생활 채집
```

검색어를 입력하면 관련 목록이 표시됩니다.
자주 쓰는 용어, 줄인말, 태그 등으로 검색할 수 있습니다
채집물은 채집물 이름으로 검색해 주세요.

초성 검색 기능, 영타 검색 기능을 지원합니다
```text
ㅇㄷㅎㅂ > 아득한빛
dkemr > 아득
```
과 같이 검색 할 수 있어요

## 업데이트

프로그램을 실행하면 자동으로 최신 앱버전과 DB가 있는지 확인합니다.
새 업데이트가 있으면 깃허브에서 내려받아 자동으로 업데이트합니다.


DB는 신규 패치가 있을 경우 제작자가 주기적으로 업데이트합니다.
잘못된 정보 / 변경점 요청은 [여기](https://github.com/madarling1/mabiDB/issues/new)에 적어주세요!
혹은 인게임 채팅으로 해주셔도 돼요! (마다링 - 던컨)


## 저장소 구조

```text
mabiDB/
  README.md
  USER_GUIDE.txt
  build.ps1         # dist/mabiDB.exe 단일 실행 파일을 생성합니다.
  config/           # 원격 DB와 버전 파일 URL을 설정합니다.
    remote_db.json
  data/
    app_version.txt # 앱 업데이트 판단 기준입니다.
    db_version.txt  # DB 업데이트 판단 기준입니다.
    mobidb.sqlite   # exe에 포함되는 기본 DB입니다.
  resources/
    schema.sql      # DB 테이블 구조를 정의합니다.
  src/
    main.py         # 터미널 UI를 실행하고 검색 흐름을 제어합니다.
    paths.py        # exe 실행 여부에 따라 앱/리소스/사용자 데이터 경로를 정합니다.
    database.py     # SQLite 연결, 기본 DB 복사, 스키마 초기화를 담당합니다.
    app_updater.py  # 앱 버전 확인, 다운로드, 교체 업데이트를 처리합니다.
    db_updater.py   # DB 버전 확인과 다운로드 업데이트를 처리합니다.
    search.py       # 검색 로직을 처리합니다.
```
