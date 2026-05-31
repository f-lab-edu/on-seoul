# Knock 연동 가이드

on-seoul 알림 서비스(SMS + 이메일)를 Knock과 연결하기 위한 설정 가이드.

---

## 1. 계정 및 환경 준비

1. [https://knock.app](https://knock.app) 에서 계정 생성
2. 대시보드 좌측 상단 워크스페이스 선택 → **Development** 환경에서 먼저 세팅한다

> Knock은 **Development / Production** 환경을 분리한다. API 키와 워크플로우가 환경별로 독립되므로, 로컬 개발은 Development, 실서비스는 Production 키를 사용한다.

---

## 2. API 키 발급

1. 대시보드 좌측 메뉴 **Developers → API Keys** 이동
2. **Secret key** 복사 (`sk_test_...` 형태 — Development 환경)
3. `application.yml`에 설정:

```yaml
knock:
  api-key: "sk_test_YOUR_SECRET_KEY"
```

> Secret key는 서버 사이드 전용이다. 절대 클라이언트(프론트엔드)에 노출하지 않는다.

---

## 3. 이메일 채널 설정

### 3-1. 이메일 제공자 연결

1. 대시보드 **Integrations → Email** 이동
2. 사용할 이메일 제공자 선택 (예: SendGrid, SES, Postmark 등)
3. 제공자 API 키 입력 후 저장
4. **From address** 설정 (예: `noreply@on-seoul.kr`)

### 3-2. 이메일 워크플로우 생성

1. 대시보드 **Workflows → Create workflow** 클릭
2. 이름: `service-change-email`, Key: `service-change-email` 입력
3. **Add step → Send email** 선택
4. 이메일 템플릿 작성:

   | 항목 | 값 |
   |---|---|
   | Subject | `{{ data.title }}` |
   | Body | `{{ data.body }}` |

5. **Save & Commit** 클릭

> `data.title`, `data.body`는 API 서비스가 Knock 트리거 시 전달하는 payload 키다 (`KnockNotificationAdapter` 참조).

---

## 4. SMS 채널 설정

### 4-1. SMS 제공자 연결

1. 대시보드 **Integrations → SMS** 이동
2. 사용할 SMS 제공자 선택 (예: Twilio, AWS SNS 등)
3. 제공자 계정 SID / Auth Token / 발신 번호 입력 후 저장

### 4-2. SMS 워크플로우 생성

1. 대시보드 **Workflows → Create workflow** 클릭
2. 이름: `service-change-sms`, Key: `service-change-sms` 입력
3. **Add step → Send SMS** 선택
4. SMS 본문 작성:

   ```
   [온서울] {{ data.title }}
   {{ data.body }}
   ```

5. **Save & Commit** 클릭

---

## 5. 수신자(Recipient) 등록

Knock은 워크플로우 트리거 시 `recipients` 배열에 전달된 ID로 수신자를 식별한다. on-seoul은 **`userId`(Long)를 문자열로 변환**하여 Knock 수신자 ID로 사용한다.

수신자에게 이메일/SMS를 실제로 발송하려면 Knock에 연락처가 등록되어 있어야 한다. 두 가지 방법 중 하나를 선택한다.

### 방법 A — 트리거 시 인라인 등록 (권장, **현재 구현 방식**)

워크플로우 트리거 payload에 수신자 정보를 포함하면 Knock이 자동으로 upsert한다:

```json
{
  "recipients": [
    {
      "id": "1",
      "email": "user@example.com",
      "phone_number": "+821012345678"
    }
  ],
  "data": {
    "title": "서비스 변경 알림",
    "body": "OA-2266 체육시설 예약 서비스가 변경되었습니다.",
    "dispatch_id": "100"
  }
}
```

**구현 흐름** (`NotificationScheduler` → `KnockNotificationAdapter`):

1. `LoadUserContactPort`가 `users` 테이블에서 `email`, `phone_number` 조회
2. 연락처 미등록 시 `UserContact(userId, null, null)` fallback — userId만으로 Knock에 수신자 식별
3. `KnockNotificationAdapter.triggerWorkflow()`가 `recipients` 배열에 `id` + (보유한 경우) `email` / `phone_number` 인라인 전달
4. 채널에 필요한 연락처가 없으면(EMAIL인데 email=null, SMS인데 phone_number=null) 해당 채널 스킵

```java
// KnockNotificationAdapter 내 recipients 구성 예시
Map<String, Object> recipientMap = new LinkedHashMap<>();
recipientMap.put("id", String.valueOf(recipient.userId()));
if (StringUtils.hasText(recipient.email()))       recipientMap.put("email", recipient.email());
if (StringUtils.hasText(recipient.phoneNumber())) recipientMap.put("phone_number", recipient.phoneNumber());
```

> 모든 채널이 스킵되면(연락처 없음) `RuntimeException("모든 채널 발송 실패")`를 던져 `notification_dispatch.status = FAILED`로 기록된다. 다음 배치에서 `last_notified_at` 미갱신으로 자동 재시도된다.

### 방법 B — Knock API로 사전 등록

```bash
curl -X PUT https://api.knock.app/v1/users/1 \
  -H "Authorization: Bearer sk_test_YOUR_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "phone_number": "+821012345678"
  }'
```

---

## 6. `application.yml` 최종 설정

```yaml
knock:
  api-key: "sk_test_YOUR_SECRET_KEY"       # Knock > Developers > API Keys
  email-workflow-key: "service-change-email"
  sms-workflow-key: "service-change-sms"
  timeout-seconds: 10
```

---

## 7. 로컬 연동 테스트

`KnockNotificationAdapterIntegrationTest`를 사용한다.
환경변수가 설정된 경우에만 실행되며, 미설정 시 자동으로 스킵된다(`@Disabled` 없이 `assumeTrue` 기반).

### 환경변수 목록

| 환경변수 | 필수 | 설명 |
|---|---|---|
| `KNOCK_API_KEY` | ✓ | Knock Secret key (`sk_test_...`) |
| `KNOCK_TEST_USER_ID` | ✓ | Knock 수신자 ID (on-seoul userId) |
| `KNOCK_TEST_EMAIL` | 이메일 테스트 시 | 수신 이메일 주소 |
| `KNOCK_TEST_PHONE` | SMS 테스트 시 | 수신 전화번호 (E.164, 예: `+821012345678`) |
| `KNOCK_EMAIL_WORKFLOW_KEY` | — | 이메일 워크플로우 키 (기본값: `service-change-email`) |
| `KNOCK_SMS_WORKFLOW_KEY` | — | SMS 워크플로우 키 (기본값: `service-change-sms`) |

### 실행

```bash
KNOCK_API_KEY=sk_test_xxx \
KNOCK_TEST_USER_ID=1 \
KNOCK_TEST_EMAIL=your@email.com \
KNOCK_TEST_PHONE=+821012345678 \
./gradlew :notification:test \
  --tests "*.KnockNotificationAdapterIntegrationTest"
```

### 결과 확인

**Knock 대시보드 → Logs** 에서 트리거 수신 및 발송 결과 확인

---

## 8. Development → Production 전환 시 체크리스트

- [ ] Production 환경 Secret key로 `knock.api-key` 교체
- [ ] Production 환경에서 이메일/SMS 워크플로우 별도 생성 및 Commit
- [ ] Production 이메일 제공자 도메인 인증(SPF, DKIM) 완료
- [ ] Production SMS 제공자 발신 번호 등록 완료
- [ ] 수신자 연락처 등록 방식 확정 (인라인 vs 사전 등록)
