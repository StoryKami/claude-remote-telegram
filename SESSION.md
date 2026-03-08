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

## TODO
- [ ] 실제 실행 테스트
- [ ] 테스트 코드 작성
- [ ] 에지 케이스 처리 보강
