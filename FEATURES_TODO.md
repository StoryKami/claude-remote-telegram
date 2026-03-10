# Feature Backlog — claude-remote-telegram

> 60+ 프로젝트 분석 기반. 우선순위: P0(즉시) > P1(다음) > P2(나중) > P3(아이디어)
> 참고 리서치: 이 파일 하단 [Sources] 섹션

## 중요도 순위 (Top 15)

| # | 기능 | 우선순위 | 이유 |
|---|------|---------|------|
| 1 | 음성 메시지 (Whisper) | P0 | 거의 모든 경쟁자가 지원. 모바일 핵심 UX |
| 2 | /model 명령어 | P0 | 기본 기능인데 없음. 즉시 추가 가능 |
| 3 | /usage + 비용 표시 | P0 | SDK ResultMessage에서 이미 cost_usd 받고 있음 |
| 4 | SDK + CLI fallback | P0 | SDK 불안정 시 서비스 연속성 보장 |
| 5 | Dual Mode (대화형/터미널) | P0 | RichardAtCT 핵심 차별점. 파워유저에게 필수 |
| 6 | 토큰/비용 모니터링 (DB) | P1 | 비용 관리. 장기 운영에 필수 |
| 7 | 프로젝트별 세션 격리 | P1 | 여러 프로젝트 동시 작업 시 필수 |
| 8 | GitHub 웹훅 연동 | P1 | 개발 워크플로우 통합. 높은 가치 |
| 9 | 알림 시스템 (Async) | P1 | 긴 작업 시 사용자 자유 보장 |
| 10 | 스케줄러 (cron) | P2 | 자동화. 있으면 좋지만 급하진 않음 |
| 11 | 웹 대시보드 | P2 | 시각적 모니터링. Telegram 보완 |
| 12 | 멀티 유저 + 관리자 | P2 | 팀 사용 시 필요 |
| 13 | MCP 서버 통합 | P2 | 확장성. 다른 도구와 연결 |
| 14 | 멀티 머신 지원 | P3 | 여러 PC 제어. 고급 사용자용 |
| 15 | 음성 TTS 응답 | P3 | hands-free. 음성 입력이 먼저 |

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

### 서브에이전트/백그라운드 작업 모니터링
- [ ] Claude가 서브에이전트를 띄우거나 백그라운드 작업을 실행할 때 Telegram에 표시
- [ ] 현재 돌고 있는 에이전트/작업 목록 조회 (/jobs 또는 /agents)
- [ ] 각 작업의 상태, 경과시간, 세부 정보 확인 가능
- [ ] 개별 작업 중단 기능

### Stop 즉시 반응
- [ ] Stop 버튼 누르면 SDK query를 즉시 중단 (현재 cancel_event 기반인데 반응이 느림)
- [ ] SDK 내부 subprocess를 직접 kill하거나, query iterator를 강제 종료하는 방식 검토
- [ ] 사용자 체감: Stop 누르면 1초 이내 멈춤

### 메시지 수정 시 재처리
- [ ] 사용자가 처리 중인 메시지를 수정하면, 현재 처리를 중단하고 수정된 메시지로 다시 처리
- [ ] aiogram의 edited_message 이벤트 핸들링
- [ ] 진행 중인 세션의 bridge.request_cancel → 수정된 프롬프트로 재실행

### 이미지/파일 프롬프트 경로 노출 제거
- [ ] 이미지 첨부 시 봇이 "I'm sharing an image. View it at: F:\..." 프롬프트를 텔레그램에 그대로 표시하는 문제
- [ ] 내부 프롬프트(경로 포함)는 사용자에게 보여주지 않고, 캡션이나 "(image)" 같은 요약만 표시
- [ ] 서버 디렉토리 구조 노출 = 보안 위험

### 토픽 이름 동기화
- [ ] 사용자가 Telegram에서 토픽 이름을 수동 변경하면 DB의 session.name도 동기화
- [ ] ChatMemberUpdated 또는 forum_topic_edited 이벤트 핸들링

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

### SDK + CLI fallback
- [ ] SDK query 실패 시 CLI subprocess로 자동 전환
- [ ] SDK 에러 시 자동 retry (1회)
- 참고: RichardAtCT (SDK primary, CLI fallback)

### Dual Mode (대화형 + 터미널)
- [ ] 대화형(agentic) 모드 — 자연어로 대화 (현재 기본)
- [ ] 터미널 모드 — 13개 명령어 + 인라인 키보드로 직접 조작
- [ ] /terminal, /chat으로 전환
- 참고: RichardAtCT (dual mode)

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
- 참고: OpenClaw, RichardAtCT (/schedule)

---

## P3 — 아이디어 (미래)

### 멀티 머신 지원
- [ ] 하나의 Telegram 그룹에서 여러 컴퓨터의 Claude Code 제어
- [ ] 머신별 토픽 or 채널
- 참고: chadingTV/claudecode-discord (한 Discord에서 여러 PC 관리)

### Kanban 세션 UI
- [ ] 세션을 칸반 보드 형태로 관리
- [ ] 세션별 목표/상태/브랜치/PR 표시
- 참고: KyleAMathews/claude-code-ui

### 음성 TTS 응답
- [ ] Claude 응답을 음성으로 변환해서 Telegram 음성 메시지로 전송
- [ ] hands-free 워크플로우
- 참고: Helmi/CCNotify (50+ 음성, 이벤트별 알림)

### 인라인 키보드 액션 버튼
- [ ] 도구 실행 결과에 액션 버튼 추가 (예: "이 파일 열기", "diff 보기")
- [ ] 대화형 워크플로우
- 참고: linuz90/claude-telegram-bot (ask_user MCP)

### SMS 기반 제어 (최소 인프라)
- [ ] SMS로 명령 보내고 결과 받기
- [ ] 인터넷 앱 없이도 작동
- 참고: cyzhao/claude-code-tools

### 세션 컨텍스트 이전 (worktree)
- [ ] git worktree 간 세션 데이터 복사
- [ ] 브랜치 전환 시 대화 기록 유지
- 참고: kbwo/ccmanager

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
| claudecodeui | Web | CLI wrapper | 7.8k stars, IDE-like 브라우저 UX |
| claude-squad | Terminal | worktree | 6.3k stars, 멀티 에이전트 병렬 관리 |
| afk-code | Telegram/Discord/Slack | PTY | 플랫폼 무관, 이미지 자동 업로드 |
| chadingTV | Discord | SDK | 멀티 머신 관리 (1 Discord → N PC) |
| claude-notifications-go | 웹훅 | hooks | 엔터프라이즈급 알림 (circuit breaker) |
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
