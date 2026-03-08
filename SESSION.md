# claude-remote-telegram

## 날짜
2026-03-09

## 상태
진행중

## 목적
Telegram을 통해 Claude와 대화하고 서버에서 코드를 실행할 수 있는 원격 코딩 어시스턴트 봇

## 기술 스택
- Python 3.11+, aiogram 3.x, anthropic SDK
- SQLite (aiosqlite) for session persistence
- Docker for portability

## 주요 결정사항
- aiogram 3.x (async Telegram bot framework)
- Claude API tool use로 bash/file 도구 4종 제공
- SQLite로 세션/메시지 영속화
- pydantic-settings로 .env 기반 설정
- Windows/Linux 호환 (platform 감지로 shell 분기)

## 구현 완료
- [x] 프로젝트 구조 생성
- [x] config (pydantic-settings)
- [x] session models, repository, manager
- [x] security (auth, sandbox)
- [x] tools (bash, file_read, file_write, file_list)
- [x] Claude API client (streaming + tool_use loop)
- [x] Telegram bot handlers (commands + message handler)
- [x] Docker support
- [x] 실제 실행 테스트 — 정상 작동 확인, resume도 동작함
- [x] /mode plan|code 명령어 (plan 모드: 분석만, code 모드: 전체 권한)
- [x] "/" 슬래시 메시지 버그 수정 — 봇 명령어 외 "/plan" 등은 Claude에 전달
- [x] help 텍스트에 Claude 스킬 목록 추가
- [x] 이미지/파일 첨부 지원 — 사진, PDF 등을 _tmp/telegram/에 저장 후 Claude에 경로 전달
- [x] 메시지 큐잉 — 작업 중 메시지를 큐에 넣어 순차 처리 (please wait 대신)
- [x] 처리 로직 리팩토링 — _process_prompt, _process_with_queue 추출
- [x] Thinking 애니메이션 (braille 스피너 + 경과시간)
- [x] /pull — git pull + 재시작 (원격 배포)
- [x] /restart — 봇 자체 재시작 (os.execv)
- [x] 재시작 알림 메시지 전송
- [x] Telegram 명령어 자동완성 (set_my_commands)
- [x] /local — 로컬 Claude 세션 목록 조회 + resume
- [x] /local peek — 세션 전환 없이 최근 대화 미리보기
- [x] 10MB subprocess 버퍼 — 이미지 JSON 라인 처리

## TODO
- [ ] 테스트 코드 작성
- [ ] 에지 케이스 처리 보강
- [ ] 음성 메시지 지원 (whisper 등)

## 작업 규칙
- 변경사항은 항상 commit & push
