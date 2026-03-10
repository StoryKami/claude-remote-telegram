# Feature Backlog — claude-remote-telegram

> 40+ 프로젝트 분석 기반. 우선순위: P0(즉시) > P1(다음) > P2(나중) > P3(아이디어)
> 참고 리서치: 이 파일 하단 [Sources] 섹션

## 현재 구현 완료

- [x] Telegram DM + Forum Topics (세션별 토픽)
- [x] claude-code-sdk 기반 bridge
- [x] 세션 관리 (new, switch, delete, close, rename)
- [x] /local — 로컬 Claude 세션 조회 + resume + clone(fork)
- [x] 이미지/파일/문서 첨부 (미디어 그룹 묶기 포함)
- [x] 메시지 큐잉 (세션별 lock)
- [x] /mode code|safe|plan — permission 승인 UI (Allow/Deny 버튼)
- [x] 실시간 상태 표시 (스피너, 도구 단계, thinking, expandable log)
- [x] /pull + /restart 원격 관리
- [x] 자동 재시작 (crash recovery)
- [x] 로그 파일 (data/bot.log)
- [x] Stop/Cancel (subprocess kill)
- [x] 보안 강화 (session_id 검증, SQL whitelist, 에러 산제화)

---

## P0 — 즉시 구현 (사용성 핵심)

### 음성 메시지 지원
- [ ] Telegram 음성 메시지 → whisper/speech-to-text → Claude에 전달
- [ ] (선택) Claude 응답 → TTS → 음성으로 회신
- 참고: RichardAtCT/claude-code-telegram, Claudegram

### /model 명령어
- [ ] 현재 모델 표시 + 변경 (sonnet, opus, haiku 등)
- [ ] 세션별 모델 설정 지원

### /usage 명령어
- [ ] 현재 세션 비용/토큰 사용량 표시
- [ ] ResultMessage에서 cost_usd 추적 + 누적
- 참고: opcode, Claude-Code-Usage-Monitor

### 에러 복구 개선
- [ ] SDK query 실패 시 자동 retry (1회)
- [ ] "Session not found" 등 일반적 에러 → 사용자 친화적 메시지

---

## P1 — 다음 구현 (경쟁력)

### 토큰/비용 모니터링
- [ ] 세션별, 일별 비용 추적 (DB 저장)
- [ ] /cost — 오늘/이번 달 비용 요약
- [ ] 비용 한도 설정 + 경고
- 참고: opcode, Claude-Code-Usage-Monitor

### 알림 시스템 (Async-first)
- [ ] 긴 작업 완료 시 알림 (다른 토픽에 있을 때)
- [ ] "fire-and-forget" 패턴 — 작업 던지고 나중에 결과 확인
- 참고: Claude-Code-Remote, Claude Push

### 웹 대시보드 (간단)
- [ ] 세션 목록 + 상태 모니터링
- [ ] 비용/토큰 그래프
- [ ] QR 코드로 모바일 접속
- 참고: 247-claude-code-remote, claude-code-monitor

### 프로젝트별 세션 격리
- [ ] /project <path> — 작업 디렉토리 지정
- [ ] 토픽 생성 시 프로젝트 경로 선택
- [ ] 프로젝트별 CLAUDE.md 자동 로드
- 참고: RichardAtCT/claude-code-telegram

### GitHub 웹훅 연동
- [ ] PR/이슈 이벤트 → Telegram 알림
- [ ] PR 리뷰 요청 → Claude가 자동 리뷰
- [ ] 이슈 → Claude가 분석 + 해결책 제안
- 참고: claude-code-action

---

## P2 — 나중 구현 (차별화)

### MCP 서버 통합
- [ ] 봇 자체를 MCP 서버로도 노출 (다른 Claude에서 도구로 사용)
- [ ] MCP 서버 관리 UI (/mcp list, /mcp add)
- 참고: discordmcp, WhatsApp MCP

### 세션 기록 + AI 요약
- [ ] 세션 종료 시 자동 요약 생성
- [ ] 검색 가능한 히스토리
- [ ] 다음 세션에 관련 컨텍스트 자동 주입
- 참고: claude-mem, claude-run

### 멀티 유저 + 팀 기능
- [ ] ADMIN_USER_IDS — /pull, /restart 등 관리자 전용
- [ ] 유저별 비용 한도
- [ ] 세션 공유 (읽기 전용 모니터링)
- 참고: Slack integration, Disclaude

### Cloudflare Tunnel 자동 프로비저닝
- [ ] 웹 대시보드 외부 접근용
- [ ] 설정 없이 자동 터널 생성
- 참고: 247-claude-code-remote, claude-code-desktop-remote

### 스케줄러 (cron)
- [ ] /schedule "매일 오전 9시" "git pull && npm test"
- [ ] 예약 작업 결과 알림
- [ ] 반복 작업 관리
- 참고: OpenClaw

---

## P3 — 아이디어 (미래)

### Desktop Companion App
- [ ] Tauri/Electron 기반 데스크탑 앱
- [ ] Telegram + 웹 + 데스크탑 동시 지원
- 참고: opcode, CodePilot

### Email 알림/제어
- [ ] 긴 작업 결과를 이메일로
- [ ] 이메일 회신으로 다음 명령
- 참고: Claude-Code-Remote

### 자동 스킬 감지
- [ ] 프로젝트 타입에 따라 적절한 스킬 자동 활성화
- [ ] hooks로 프로젝트 분석 → 맞춤 설정
- 참고: Claude Code Infrastructure Showcase

### 세션 핸드오프
- [ ] 팀원에게 세션 이전 (Telegram → Slack → Desktop)
- [ ] 크로스 플랫폼 세션 연속성
- 참고: Happy Coder

### 코드 검색 통합
- [ ] 벡터 검색으로 코드베이스 전체를 컨텍스트로
- 참고: zilliztech/claude-context

---

## 경쟁 분석 요약

| 프로젝트 | 플랫폼 | 아키텍처 | 핵심 차별점 |
|----------|--------|----------|------------|
| OpenClaw | WhatsApp/Telegram/Signal | SDK | 500+ 앱 통합, 개인 AI |
| RichardAtCT | Telegram | SDK | 음성, PDF, 프로젝트 격리 |
| Disclaude | Discord | tmux | 채널=세션, ANSI 컬러 |
| Happy Coder | iOS/Android | Native | E2E 암호화, 자체호스팅 |
| 247 | Web | Cloudflare Tunnel | 모바일 우선, 터치 최적화 |
| opcode | Desktop | Tauri | 토큰 모니터링, kill switch |
| Claude Push | 알림 | bash+ntfy | 60줄, 3분 셋업 |
| **우리 (claude-remote-telegram)** | **Telegram** | **SDK** | **Forum Topics, Clone/Fork, Safe mode, expandable log** |

---

## Sources

- [OpenClaw](https://github.com/openclaw/openclaw)
- [RichardAtCT/claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram)
- [JessyTsui/Claude-Code-Remote](https://github.com/JessyTsui/Claude-Code-Remote)
- [zebbern/claude-code-discord](https://github.com/zebbern/claude-code-discord)
- [Disclaude](https://disclaude.com/)
- [Happy Coder](https://happy.engineering/)
- [QuivrHQ/247-claude-code-remote](https://github.com/QuivrHQ/247-claude-code-remote)
- [HLE-C0DE/claude-code-desktop-remote](https://github.com/HLE-C0DE/claude-code-desktop-remote)
- [winfunc/opcode](https://github.com/winfunc/opcode)
- [onikan27/claude-code-monitor](https://github.com/onikan27/claude-code-monitor)
- [kill136/claude-code-open](https://github.com/kill136/claude-code-open)
- [Claude Code Headless Docs](https://code.claude.com/docs/en/headless)
- [Claude Code SDK Docs](https://platform.claude.com/docs/en/agent-sdk/python)
- [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
