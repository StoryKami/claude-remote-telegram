# Feature Implementation Prompt

> 이 프롬프트를 새 Claude 세션에 붙여넣으면 FEATURES_TODO.md를 기반으로 기능을 구현할 수 있습니다.

---

## 프롬프트

```
이 프로젝트는 Telegram으로 Claude Code를 원격 제어하는 봇이야.

1. 먼저 다음 파일들을 읽어:
   - F:\Programming\claude_workspace\claude-remote-telegram\FEATURES_TODO.md (기능 백로그)
   - F:\Programming\claude_workspace\claude-remote-telegram\SESSION.md (작업 기록)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\claude\bridge.py (SDK 브릿지)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\bot\handlers.py (핸들러)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\config.py (설정)

2. FEATURES_TODO.md에서 체크 안 된 항목 중 [원하는 우선순위/기능]을 구현해줘.

3. 작업 규칙:
   - 변경사항은 항상 commit & push
   - /pull로 봇에 적용 가능
   - 테스트는 텔레그램에서 직접
   - handlers.py가 큰 파일이니 수정 시 주의

4. 기술 스택:
   - Python 3.11+, aiogram 3.x, claude-code-sdk
   - SQLite (aiosqlite), pydantic-settings
   - Telegram Forum Topics 지원
```

---

## 우선순위별 추천 구현 순서

### 빠르게 끝나는 것 (30분 이내)
1. `/model` 명령어
2. `/usage` 명령어
3. 에러 복구 개선

### 중간 작업 (1-2시간)
4. 음성 메시지 지원 (whisper)
5. 토큰/비용 모니터링
6. 알림 시스템

### 큰 작업 (반나절+)
7. 웹 대시보드
8. GitHub 웹훅 연동
9. MCP 서버 통합
