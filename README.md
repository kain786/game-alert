# 게임알리미 · 개인용 배포판

기존 게임알리미의 원신·젠레스·스타레일 일정 수집과 화면을 재사용한 독립 배포판입니다. 받는 사람의 **Mac mini + 기존 Caddy + Discord 개인 DM**을 기본으로 합니다. 여러 사용자 가입, 공통 로그인 서버, 도독이의 운영 인프라는 필요하지 않습니다.

- 픽업 시작·진행 중·마감 임박, 공식 방송의 한국 시간 표시, 게임 필터와 ICS 캘린더 다운로드.
- 위키 수집은 웹과 별도 프로세스에서 실행됩니다. 소스 오류 때 마지막 정상 자료를 유지합니다.
- 수집은 시작할 때와 이후 6시간마다, 알림 검사는 10분마다 실행합니다. 수동 갱신은 약 5초 내 작업자가 확인합니다.
- 자동 DM은 기본 **꺼짐**입니다. 켠 뒤 첫 정상 실행은 기존 일정을 기준선으로 저장하며, 이미 등록된 예정 일정을 한꺼번에 보내지 않습니다. 이후 새 일정·변경·픽업 종료일 전날 또는 당일 안내를 보냅니다. 기존 동작처럼 발송 시간은 **10:00~20:00 KST**입니다.
- 전송 실패는 대기열에 남고, 앞서 성공한 알림은 반복하지 않습니다. Discord nonce는 짧은 시간의 재시도 중복을 줄이지만 모든 장애에서 중복을 완전히 없애는 보장은 없습니다.

## Mac mini 설치

Python **3.12**와 기존 Caddy가 필요합니다. macOS 11 이상 Apple Silicon용 의존성 파일을 확인했습니다. 실제 받는 사람의 맥에서 설치·자동 시작·DM 수신까지 확인한 것은 아닙니다. 이 설치 스크립트는 Homebrew, Caddy, 운영체제 설정을 설치하거나 바꾸지 않습니다.

공개 GitHub 저장소에서 **Code → Download ZIP**을 눌러 내려받습니다. GitHub 계정은 필요하지 않습니다. 압축을 풀고 폴더를 계속 사용할 위치에 두세요. 예: `~/Applications/game-alert`. 저장소 주소는 [kain786/game-alert](https://github.com/kain786/game-alert)이며 [ZIP 바로 받기](https://github.com/kain786/game-alert/archive/refs/heads/main.zip)도 사용할 수 있습니다.

```bash
cd ~/Applications/game-alert
sh scripts/setup.sh
```

생성된 `.env`에서 `PUBLIC_BASE_URL`을 본인의 HTTPS 주소로 바꿉니다. **도메인 루트 전용**이며 `/game/`처럼 하위 경로에는 설치하지 않습니다. 기본 포트는 `18136`입니다. 비어 있는 `GEVENT_CACHE_DIR`은 이 폴더의 `data/`를 사용합니다. 자동 DM은 우선 `false`로 둡니다.

`.discord.env`에는 본인의 `DISCORD_BOT_TOKEN`과 DM을 받을 본인의 `DISCORD_USER_ID`를 넣습니다. 값은 이 파일에만 보관하고 Git에 올리지 않습니다. 파일은 셸 코드가 아닌 단순 `이름=값` 형식입니다.

```bash
sh scripts/install-macos.sh
# python3.12 명령이 다른 위치에 있는 경우:
# GAME_ALERT_PYTHON=/absolute/path/to/python3.12 sh scripts/install-macos.sh
curl --fail http://127.0.0.1:18136/health
.venv/bin/python scripts/macos_service.py status
```

설치기는 전용 `.venv`와 현재 로그인 사용자의 LaunchAgent 3개를 만듭니다. `sudo`로 실행하지 마세요. 로그인된 GUI 세션이 필요합니다. 컴퓨터가 깨어 있고 해당 사용자가 로그인한 동안 동작합니다. 재부팅 후 로그인하기 전과 잠자기 중에는 수집·발신하지 않습니다. 절전·자동 로그인 설정은 설치기가 바꾸지 않습니다.

## 기존 Caddy 연결

`deploy/Caddyfile.example`의 **사이트 블록 하나만** 기존 Caddy 설정에 추가합니다. 전체 Caddyfile을 예제로 덮어쓰지 마세요.

1. 예제 도메인을 `.env`의 `PUBLIC_BASE_URL`과 같은 도메인으로 바꿉니다.
2. `caddy hash-password`를 실행해 로그인 비밀번호의 해시를 생성합니다. 예제의 사용자 이름과 해시 자리표시자를 바꿉니다.
3. 포트를 바꾸었다면 `reverse_proxy` 포트와 `.env`의 `WEB_PORT`도 맞춥니다.
4. 본인의 실제 Caddyfile 경로로 검증한 다음, 기존 Caddy 관리 방식으로 reload합니다.

```bash
caddy validate --config /actual/path/to/Caddyfile --adapter caddyfile
caddy reload --config /actual/path/to/Caddyfile --adapter caddyfile
```

웹은 `127.0.0.1`에서만 수신합니다. 외부에서 들어올 때는 기존 Caddy의 HTTPS와 예제의 Basic 인증을 거칩니다. 이 배포판은 자체 로그인 화면이 없으므로 인증 설정도 함께 적용하세요. Caddy가 같은 맥에서 실행되는 예제입니다. 이미 다른 인증을 쓰고 있다면 그 관리자가 동일한 접근 보호를 적용할 수 있습니다. [Caddy 공식 Basic 인증 안내](https://caddyserver.com/docs/caddyfile/directives/basic_auth).

## Discord 개인 DM 준비

1. [Discord Developer Portal](https://discord.com/developers/applications)에서 본인의 애플리케이션과 봇을 준비하고, **봇 토큰**을 `.discord.env`에 입력합니다. 개인 계정 토큰·웹훅 주소는 쓰지 않습니다.
2. 해당 봇을 본인의 테스트 서버에 초대해 같은 서버에 있게 합니다. DM 발신만을 위해 Administrator 권한은 필요하지 않습니다.
3. Discord 설정의 개발자 모드를 켜고 본인 계정의 **사용자 ID 복사** 값을 `DISCORD_USER_ID`에 입력합니다.
4. 공통 서버 멤버에게서 DM을 받을 수 있게 하고 봇을 차단하지 않습니다. 실제 DM 가능 여부는 아래 테스트로 확인합니다.

```bash
# 봇 자격증명만 확인합니다. DM은 보내지 않습니다.
.venv/bin/python source/runtime.py discord-check
# 명시적으로 테스트 DM 1개를 보냅니다. 일정 알림 기록은 바꾸지 않습니다.
.venv/bin/python source/runtime.py discord-test
```

성공하면 `.env`의 `NOTIFICATIONS_ENABLED=true`로 바꾸고 본인 LaunchAgents를 다시 설치하여 설정을 반영합니다.

```bash
.venv/bin/python scripts/macos_service.py install
```

받는 사람 한 명에게 REST API로만 전송하므로 Gateway 연결이나 Message Content Intent는 사용하지 않습니다. `401`은 봇 토큰, `403`은 DM 접근 조건을 확인합니다. 실패한 DM은 대기열에 남습니다. 전송 제한의 대기 시간은 존중하며 토큰·응답 본문은 로그에 출력하지 않습니다. [Discord DM API](https://docs.discord.com/developers/resources/user#create-dm), [메시지 API](https://docs.discord.com/developers/resources/message#create-message), [전송 제한](https://docs.discord.com/developers/topics/rate-limits).

## 상태·중지·보존

```bash
.venv/bin/python scripts/macos_service.py status
.venv/bin/python source/runtime.py dry-run
# 현재 작업 종료 후 이 배포판의 LaunchAgents만 중지합니다.
.venv/bin/python scripts/macos_service.py stop
# 자동 시작 등록 제거. 설정·일정·알림 기록은 보존합니다.
.venv/bin/python scripts/macos_service.py uninstall
```

로그는 `~/Library/Logs/game-alert/`의 구성요소별 `.log`, `.error.log`입니다. 장기 운영 시 파일 크기를 확인하세요. 마지막 수집 성공 시각은 화면의 **출처와 마지막 정상 갱신**에서 확인합니다. 웹 health 성공은 수집·DM 성공을 뜻하지 않습니다.

중지 후 `data/`, `.env`, `.discord.env`를 비공개 위치에 백업하세요. 기존 `notify_state.json`을 지우거나 예전 사본으로 되돌리면 기준선이나 전송 기록이 달라질 수 있습니다. 업그레이드할 때 이 세 경로를 보존하고 먼저 작업자를 중지한 후 소스·의존성을 갱신하세요. 실행 폴더를 이동할 때는 기존 위치에서 `uninstall` 후 새 위치에서 설치합니다.

## Docker 대안

맥에서 Docker를 이미 운영한다면 직접 실행 대신 Compose를 선택할 수 있습니다. **LaunchAgents와 Compose를 동시에 실행하지 마세요.** 두 방식의 데이터 저장 위치도 다릅니다.

```bash
sh scripts/setup.sh
# .env / .discord.env 수정 후
docker compose up -d --build
docker compose ps
docker compose run --rm --no-deps notifier python discord_cli.py --check
# 명시적으로 테스트 DM 1개:
docker compose run --rm --no-deps notifier python discord_cli.py --send-test
# NOTIFICATIONS_ENABLED=true 변경 후:
docker compose up -d notifier
# 작업 종료를 기다리며 중지, 데이터 볼륨은 보존:
docker compose down
```

웹 포트는 호스트 loopback으로만 공개하고 알림 봇 환경변수는 notifier 컨테이너에만 전달합니다. 데이터는 `schedule-data` 이름의 Compose 볼륨입니다. 볼륨 삭제 옵션은 사용하지 마세요. Docker 이미지 실제 빌드는 Linux x86_64에서 확인했으며 Mac Docker의 ARM64 이미지 실행은 미검증입니다. Mac에서는 위의 Python/LaunchAgent 방식이 기본입니다.

## 개발·검증

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=source .venv/bin/python -m unittest discover -s tests -v
```

테스트는 임시 캐시와 모의 HTTP 응답만 사용하고 실제 DM·운영 캐시를 사용하지 않습니다. 검증 내역과 한계는 `VALIDATION.md`, 원본 정보는 `PROVENANCE.md`를 확인하세요.
