# Feature Implementation Prompt

> 새 Claude 세션에서 이 프로젝트의 기능을 구현할 때 사용하는 가이드입니다.

---

## 사용법

새 세션(텔레그램 토픽, 데스크탑 Claude Code 등)에서 아래 프롬프트를 보내세요:

---

### 프롬프트 (복사해서 붙여넣기)

```
이 프로젝트는 Telegram으로 Claude Code를 원격 제어하는 봇이야.

1. 먼저 다음 파일들을 읽어:
   - F:\Programming\claude_workspace\claude-remote-telegram\FEATURES_TODO.md (기능 백로그 + 우선순위)
   - F:\Programming\claude_workspace\claude-remote-telegram\SESSION.md (작업 기록)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\claude\bridge.py (SDK 브릿지)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\bot\handlers.py (핸들러, ~1000줄)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\config.py (설정)
   - F:\Programming\claude_workspace\claude-remote-telegram\src\session\manager.py (세션 관리)

2. FEATURES_TODO.md의 "중요도 순위 (Top 15)" 테이블을 확인해.
   체크 안 된 항목 중 [원하는 기능]을 구현해줘.

3. 작업 규칙:
   - 변경사항은 항상 commit & push
   - 텔레그램에서 /pull로 봇에 즉시 적용 가능
   - 테스트는 텔레그램에서 직접
   - handlers.py가 큰 파일이니 수정 시 주의 (전체 Write 대신 Edit 사용)
   - FEATURES_TODO.md에서 완료한 항목은 [x]로 체크

4. 기술 스택:
   - Python 3.11+, aiogram 3.x, claude-code-sdk
   - SQLite (aiosqlite), pydantic-settings
   - Telegram Forum Topics 지원
   - bridge.py: SDK query() 기반 (subprocess 아님)

5. 현재 아키텍처:
   - bridge.py: claude-code-sdk의 query()로 메시지 전송, StreamEvent로 이벤트 전달
   - handlers.py: setup_handlers() 클로저 패턴, _StatusTracker로 상태 표시
   - 세션: SQLite DB + topic_id로 Forum 토픽 연결
   - 인증: allowed_user_ids 기반 + AuthMiddleware
```

---

## 구현 난이도별 가이드

### 쉬움 (30분 이내)
| 기능 | 힌트 |
|------|------|
| `/model` | config.py에서 모델명 관리, bridge에 전달, `/mode`와 유사한 버튼 UI |
| `/usage` | ResultMessage에서 cost_usd 수집, DB에 누적, 세션별 합산 |

### 중간 (1-2시간)
| 기능 | 힌트 |
|------|------|
| 음성 메시지 | aiogram F.voice 핸들러, whisper API or local, ogg→text→prompt |
| SDK+CLI fallback | bridge.py에 try SDK → except → CLI subprocess 로직 |
| 비용 모니터링 DB | sessions 테이블에 total_cost 컬럼, ResultMessage에서 업데이트 |

### 어려움 (반나절+)
| 기능 | 힌트 |
|------|------|
| Dual Mode | 터미널 모드: 인라인 키보드로 도구 직접 선택 (Read/Write/Bash 등) |
| GitHub 웹훅 | aiogram webhook 대신 별도 aiohttp 서버, PR/이슈 이벤트 파싱 |
| 웹 대시보드 | aiohttp or FastAPI, 세션 목록 + 비용 그래프, QR 코드 |
| 스케줄러 | APScheduler or asyncio cron, DB에 스케줄 저장, 결과 알림 |

---

## 주의사항

- `.env` 파일은 커밋하지 마세요 (토큰 포함)
- LOG_LEVEL=DEBUG 상태이면 로그가 많이 쌓여요 (data/bot.log)
- handlers.py가 ~1000줄로 큰 편 — 새 기능은 가능하면 별도 모듈로
- Forum Topics 모드에서는 message.message_thread_id로 세션 라우팅
