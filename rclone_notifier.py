import time
import requests

# Rclone RC 서버 설정
RCLONE_URL = "http://192.168.0.90:5274"
AUTH = None  # Rclone RC 인증 정보가 있다면 ("id", "password") 형태로 입력

# 디스코드 웹훅 URL 설정
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1506257436686942319/VyHjxCGA-BBtHFbegQIHwWiJLrWvlm-AF9s59yl3KJ3RyS5U9O7NVqDDW_XYXEc3wgtk"

# 주기 설정 (초 단위)
CHECK_INTERVAL = 1800   # 30분마다 Rclone 전송 내역 확인 (30 * 60초)
REFRESH_INTERVAL = 3600 # 1시간마다 Rclone 캐시/마운트 새로고침 (필요시 3600초)
TARGET_DIR = "mnt/gds2/GDRIVE/READING"


def send_discord_notification(file_path, file_size):
    """디스코드 웹훅으로 메시지를 전송하는 함수"""
    size_mb = file_size / 1024 / 1024
    
    content = (
        "🚨 **[RCLONE] 새로운 파일 추가/변경 감지**\n"
        f"📂 **경로:** `{file_path}`\n"
        f"📦 **용량:** `{size_mb:.2f} MB`"
    )
    
    payload = {
        "content": content
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if response.status_code == 204:
            print(f"[디스코드 전송 성공] {file_path}")
        else:
            print(f"[디스코드 전송 실패] 상태 코드: {response.status_code}, 내용: {response.text}")
    except Exception as e:
        print(f"[디스코드 통신 에러] {e}")


def refresh_rclone_vfs():
    """Rclone 마운트/캐시 새로고침 요청"""
    try:
        payload = {"dir": TARGET_DIR, "recursive": True}
        res = requests.post(f"{RCLONE_URL}/vfs/refresh", json=payload, auth=AUTH, timeout=10)
        if res.status_code == 200:
            print(f"[*] Rclone VFS 새로고침 완료 ({TARGET_DIR})")
    except Exception as e:
        pass


def monitor_rclone():
    print(f"[*] Rclone 모니터링 시작: {RCLONE_URL}")
    print(f"[*] 감시 대상 폴더: [{TARGET_DIR}]")
    print(f"[*] 체크 주기: 30분 마다 실행됩니다.\n")

    seen_files = set()
    last_refresh_time = 0

    while True:
        current_time = time.time()

        # 지정된 주기(1시간)마다 Rclone VFS 새로고침 수행
        if current_time - last_refresh_time >= REFRESH_INTERVAL:
            refresh_rclone_vfs()
            last_refresh_time = current_time

        try:
            # Rclone 전송 완료 내역 조회
            trans_res = requests.post(f"{RCLONE_URL}/core/transferred", auth=AUTH, timeout=5)
            if trans_res.status_code == 200:
                transferred_list = trans_res.json().get("transferred", [])

                for item in transferred_list:
                    file_path = item.get("name")
                    file_size = item.get("size", 0)
                    transfer_time = item.get("time")

                    # 지정한 하부 폴더(TARGET_DIR)로 시작하는 파일인지 확인
                    if file_path and file_path.startswith(TARGET_DIR):
                        unique_key = f"{file_path}_{transfer_time}"

                        # 중복 알림 방지
                        if unique_key not in seen_files:
                            seen_files.add(unique_key)

                            # 메모리 관리를 위해 최근 1000개만 유지
                            if len(seen_files) > 1000:
                                seen_files.pop()

                            # 디스코드 노티 함수 호출
                            send_discord_notification(file_path, file_size)

        except requests.exceptions.ConnectionError:
            print(f"[-] Rclone 서버({RCLONE_URL})에 연결할 수 없습니다. 재시도 중...", end="\r")
        except Exception as e:
            print(f"[-] 에러 발생: {e}")

        # 30분 동안 대기
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        monitor_rclone()
    except KeyboardInterrupt:
        print("\n[*] 사용자에 의해 모니터링이 중단되었습니다.")
