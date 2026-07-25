# 🚀 Rclone to BookOasis Discord Notifier & Auto-Scanner

Rclone RC 서버의 파일 전송/변동 내역을 실시간으로 감시하고, 하위 폴더 단위로 묶어서 SQLite DB(`BookOasis`)와 대조한 뒤 **자동 스캔 웹훅(API)을 호출**하고 **디스코드로 결과를 요약 알림**해주는 파이썬 자동화 스크립트입니다.

---

## 🛠 주요 기능 (Features)
1. **주기적 모니터링**: 30분(설정 변경 가능)마다 Rclone RC 서버(`/core/transferred`)를 체크하여 새로운 파일 추가/변경 내역 감지
2. **VFS 캐시 자동 갱신**: 주기적으로 `vfs/refresh`를 호출하여 클라우드 마운트 상태 최신화 유지
3. **폴더 단위 압축 (Bundling)**: 수십 장의 이미지 파일(`001.WEBP`, `002.WEBP` 등)이 추가되어도 상위 폴더 단위(`DSFGDSG`, `DGFDSG`)로 묶어서 처리
4. **SQLite DB 매칭**: `media_general.db`와 `media_adult.db`의 `books` 테이블을 탐색(`LIKE` 검색)하여 일치하는 `library_id` 탐색
5. **자동 웹훅 API 호출**: 탐색된 `library_id`와 타입(`general`/`adult`)에 맞춰 BookOasis 스캔 API 호출 (중복 호출 방지 로직 탑재)
6. **디스코드 실시간 노티**: 처리된 결과(폴더 경로, 파일 개수, 용량, 매칭된 DB, API 성공 여부)를 디스코드 웹훅으로 요약 전송

---

## 📂 파일 구조 (File Structure)
```text
/mnt/rclone2webhook/
 ├── rclone_notifier.py     # 메인 파이썬 실행 스크립트
 ├── rclone2webhook.env     # 설정 환경 변수 파일
 ├── rclone_log.out         # 백그라운드 실행 로그 파일
 └── venv/                  # 파이썬 가상 환경 폴더

RCLONE_URL=http://192.168.0.90:5274
RCLONE_AUTH_USER=
RCLONE_AUTH_PASS=
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN
TARGET_DIR=mnt/gds2/GDRIVE/READING

# SQLite DB 경로 설정
DB_GENERAL_PATH=/root/docker/BookOasis_stable/db/media_general.db
DB_ADULT_PATH=/root/docker/BookOasis_stable/db/media_adult.db

# 주기 설정 (초 단위)
CHECK_INTERVAL=1800   # 30분마다 Rclone 변경 내역 확인
REFRESH_INTERVAL=3600 # 1시간마다 Rclone VFS 새로고침
