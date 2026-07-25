1. 가상 환경 생성 및 활성화
현재 작업 중인 폴더(/mnt/rclone2webhook) 안에서 아래 명령어를 차례대로 입력합니다.
code
Bash
# 1. 'venv'라는 이름의 가상 환경 생성
python3 -m venv venv

# 2. 가상 환경 활성화
source venv/bin/activate
(성공하면 터미널 커서 앞에 (venv)가 붙는 것을 확인할 수 있습니다.)

2. 라이브러리 설치
가상 환경이 활성화된 상태에서 pip로 필요한 패키지를 설치합니다. (이제 에러 없이 정상적으로 설치됩니다.)
code
Bash
pip install python-dotenv requests

3. 코드 실행 확인
가상 환경 내에서 파이썬 코드를 실행합니다.
code
Bash
python3 rclone_notifier.py

💡 백그라운드 서비스(24시간 자동 실행)로 등록하여 사용하려면?
만약 시스템을 재부팅하거나 터미널 창을 닫아도 계속 유지되게 하려면, 방금 만든 가상 환경의 파이썬 경로(venv/bin/python)를 지정해서 nohup으로 백그라운드 실행을 하시면 됩니다.
code
Bash
nohup /mnt/rclone2webhook/venv/bin/python /mnt/rclone2webhook/rclone_notifier.py > rclone_log.out 2>&1 &
