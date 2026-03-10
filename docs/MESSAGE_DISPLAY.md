# Message Display 설계

## 현재 문제

### 스크린샷 분석 (2026-03-10)
```
[유저 메시지]
  ⏳ grep: can_1m|...            ← 라이브 status (Stop 버튼)
  중간 텍스트 "Now update..."    ← 텍스트 프리뷰가 status에 노출
  25s

✓ grep: ... (25s)               ← flush된 tool 요약 (expandable)
✓ edit: ... (40s)

Now update the keyboard...       ← 중간 텍스트가 별도 메시지로 전송됨 (노이즈!)

✓ grep: ... (46s)               ← 또 다른 tool 요약
✓ read: ... (46s)

Also update the model switch...  ← 또 다른 중간 텍스트

✅ Done (61s)                    ← 최종 요약 (전체 steps expandable)
✓ read: ... (10s)
✓ edit: ... (18s)
...
```

### 핵심 문제 3가지

1. **중간 텍스트가 너무 많이 노출됨**
   - Claude가 생각하며 내뱉는 중간 텍스트 ("Now update the keyboard...")가 별도 메시지로 전송됨
   - 이건 최종 응답이 아니라 Claude의 작업 코멘터리 — 유저에게 노이즈

2. **메시지 수가 너무 많음**
   - 그룹마다: [tool 요약] + [텍스트 메시지] + [다음 status] = 3개씩 증가
   - 10개 tool 호출이면 채팅이 15~20개 메시지로 도배됨

3. **라이브 status에 텍스트 프리뷰가 지저분함**
   - status에 `last_text` (writing preview)가 보이는데, 매 refresh마다 변경되어 깜빡임

---

## 이상적인 UX

```
[유저 메시지]

⠹ Working... edit: handlers.py       ← 단 1개의 라이브 status
  💭 thinking snippet...               (3초마다 갱신)
  ✓ grep: ... (25s)                   (완료된 steps는 expandable)
  ✓ edit: ... (40s)
  ⏱ 45s
  [■ Stop] [■ Stop All]

── 작업 완료 후 ──

✅ Done (61s)                          ← status가 완료 요약으로 변환
  ✓ read: ... (10s)
  ✓ edit: ... (18s)                    (전체 steps, expandable)
  ...

최종 응답 텍스트만 여기에.             ← 마지막 텍스트만 전송
```

### 원칙
- **status 메시지는 항상 1개만 존재** (새로 만들지 않음, edit만)
- **중간 텍스트는 표시하지 않음** — 마지막 텍스트만 최종 응답으로 전송
- **tool steps는 status 안에서 expandable로 누적**
- **완료 시 status → 요약으로 edit, 최종 텍스트는 별도 메시지**

---

## 구현 계획

### Phase 1: 단일 status 메시지 (핵심 변경)

**현재 흐름:**
```
text → thinking → tool_use → tool_result → text → tool_use → ... → text(final)
         ↓                                   ↓
      flush_group()                     flush_group()
      finalize(html)                    finalize(html)
      new_status()                      new_status()
```

**목표 흐름:**
```
text → thinking → tool_use → tool_result → text → tool_use → ... → text(final)
         ↓                                   ↓
      (status edit만)                    (status edit만 — text는 누적만)
                                                              ↓
                                                         finalize(summary)
                                                         send(final_text)
```

**변경사항:**
1. `_flush_group()` 제거 — 중간 flush 없음
2. `new_status()` 호출 제거
3. `tracker`는 처음~끝까지 1개 메시지만 edit
4. 모든 text는 `accumulated_text`에만 누적 (중간 전송 없음)
5. 완료 시: `tracker.finalize(summary)` + `message.answer(final_text)`

**중간 텍스트 처리:**
- `event.type == "text"`: `accumulated_text`에 누적만, status에는 미표시
- status에는 thinking snippet + current tool만 표시
- 마지막 텍스트만 최종 응답으로 전송

### Phase 2: Status 렌더링 개선

```
⠹ Working... edit: handlers.py
💭 ...thinking preview...

<blockquote expandable>
✓ grep: AVAILABLE_MODELS (25s)
✓ edit: handlers.py (40s)
✓ read: handlers.py (46s)
</blockquote>
⏱ 45s
```

- 현재 tool (`current_tool`) 은 상단에 크게 표시
- thinking은 짧은 snippet으로
- 완료된 steps는 expandable blockquote (마지막 6개)
- elapsed는 맨 아래

### Phase 3: 최종 응답 포맷

```
✅ Done (61s)
<blockquote expandable>
✓ read: handlers.py (10s)
✓ edit: handlers.py (18s)
✓ grep: can_1m (25s)
✓ edit: handlers.py (40s)
</blockquote>

[최종 응답 텍스트 — Markdown 변환된 별도 메시지]
```

---

## 엣지 케이스

1. **text만 있고 tool이 없는 경우**: status → Done, text 전송
2. **tool만 있고 text가 없는 경우**: status → Done (no response)
3. **중간 text + 마지막 text 구분**: `accumulated_text` 전체를 최종 응답으로 전송
4. **텍스트가 4096자 초과**: chunk 분할해서 전송
5. **취소**: 현재까지 accumulated_text 전송 + "Stopped" 표시

---

## 변경 로그

| 날짜 | 변경 | 결과 |
|------|------|------|
| 2026-03-10 | 초안 작성 | 현재 문제 분석 완료 |
