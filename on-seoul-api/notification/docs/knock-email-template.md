# Knock 워크플로우 템플릿 (이메일 / SMS)

API Service는 상태 변경 감지 후 구독별로 **구조화 data 페이로드**를 Knock 워크플로우로 트리거한다.
사실 정보(서비스명·상태·접수기간·링크)는 **Knock 템플릿이 결정적으로 렌더링**하고,
AI는 짧은 요약(`summary`)만 생성한다. 이로써 LLM 환각 위험을 제거한다.

## data 페이로드 계약

`KnockNotificationAdapter`가 워크플로우 트리거 시 전달하는 `data` 객체. 아래 Liquid 키와 **정확히** 일치해야 한다.

```jsonc
{
  "title": "구독하신 2개 서비스 변경 알림",      // AI 또는 폴백
  "summary": "구독하신 2개 서비스에 변경이 감지되었습니다.", // AI 또는 폴백
  "services": [
    {
      "name": "강남 수영교실",        // null이면 키 생략(NON_NULL)
      "status": "예약마감",
      "area": "강남구",
      "place": "강남스포츠센터",
      "target": "성인",
      "receipt_start": "2026-05-01",
      "receipt_end": "2026-05-31",
      "url": "https://yeyak.seoul.go.kr/...",
      "image_url": "https://.../img.png",
      "changes": [
        { "label": "모집상태", "old": "접수중", "new": "예약마감" }
      ]
    }
  ],
  "dispatch_id": "900"               // 문자열로 직렬화됨
}
```

- **Knock으로 전달되는 wire 페이로드에서는 null 필드의 키 자체가 생략된다**(어댑터의 `putIfText`). Liquid에서 `{% if s.status %}...{% endif %}`로 가드할 것. 단 `services`/`changes` 배열은 비어 있어도(`[]`) 항상 포함된다. (주의: DB 스냅샷 `notification_payload`의 on-disk 형태는 Jackson 기본 직렬화라 null 키를 유지하므로 wire 형태와 다르다.)
- `changes[].label`은 이미 한글로 매핑되어 있다(`serviceStatus` → `모집상태`). camelCase는 노출되지 않는다.
- `dispatch_id`는 문자열이다.

---

## 이메일 워크플로우 (`service-change-email`)

표/카드 레이아웃 + 인라인 스타일(Outlook 호환). 본문은 아래 HTML을 Knock 대시보드의
이메일 스텝 본문에 붙여넣는다.

```html
<div style="font-family: -apple-system, 'Apple SD Gothic Neo', sans-serif; max-width: 600px; margin: 0 auto; color: #222; background: #f6f8fc; padding: 0 0 24px;">
  <!-- 브랜드 헤더 배너 -->
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background: linear-gradient(135deg, #2b50aa 0%, #4f74d6 100%); border-radius: 0 0 16px 16px;">
    <tr>
      <td style="padding: 28px 24px 24px; text-align: center;">
        <p style="margin: 0 0 10px; font-size: 13px; font-weight: bold; letter-spacing: 1px; color: #cdd9f7;">ON-SEOUL</p>
        <p style="margin: 0; font-size: 20px; font-weight: bold; color: #ffffff; line-height: 1.4;">
          온서울에서 오늘의<br>공공서비스 정보가 도착했어요! &#127881;
        </p>
      </td>
    </tr>
  </table>

  <!-- 본문 -->
  <div style="padding: 24px 24px 0;">
    <h2 style="font-size: 18px; margin: 0 0 8px;">{{ data.title }}</h2>
    {% if data.summary %}
    <p style="font-size: 14px; color: #555; margin: 0 0 20px; padding: 12px 14px; background: #eef2fb; border-left: 3px solid #2b50aa; border-radius: 6px; line-height: 1.6;">{{ data.summary }}</p>
    {% endif %}

  {% for s in data.services %}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="border: 1px solid #e5e5e5; border-radius: 8px; margin-bottom: 16px;">
    <tr>
      <td style="padding: 16px;">
        <p style="font-size: 16px; font-weight: bold; margin: 0 0 6px;">{{ s.name }}</p>
        {% if s.status %}
        <span style="display: inline-block; padding: 2px 8px; font-size: 12px; background: #f0f4ff; color: #2b50aa; border-radius: 4px;">{{ s.status }}</span>
        {% endif %}

        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top: 10px; font-size: 13px;">
          {% if s.area %}<tr><td style="color:#888; width:90px; padding:2px 0;">지역</td><td>{{ s.area }}</td></tr>{% endif %}
          {% if s.place %}<tr><td style="color:#888; padding:2px 0;">장소</td><td>{{ s.place }}</td></tr>{% endif %}
          {% if s.target %}<tr><td style="color:#888; padding:2px 0;">대상</td><td>{{ s.target }}</td></tr>{% endif %}
          {% if s.receipt_start %}<tr><td style="color:#888; padding:2px 0;">접수기간</td><td>{{ s.receipt_start }} ~ {{ s.receipt_end }}</td></tr>{% endif %}
        </table>

        {% if s.changes and s.changes.size > 0 %}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="margin-top: 10px; font-size: 13px; border-top: 1px solid #f0f0f0;">
          {% for c in s.changes %}
          <tr>
            <td style="color:#888; width:90px; padding:4px 0;">{{ c.label }}</td>
            <td><span style="color:#999; text-decoration: line-through;">{{ c.old }}</span>
                &rarr; <strong>{{ c.new }}</strong></td>
          </tr>
          {% endfor %}
        </table>
        {% endif %}

        {% if s.url %}
        <p style="margin: 14px 0 0;">
          <a href="{{ s.url }}" style="display:inline-block; padding:8px 14px; background:#2b50aa; color:#fff; text-decoration:none; border-radius:6px; font-size:13px;">자세히 보기</a>
        </p>
        {% endif %}
      </td>
    </tr>
  </table>
  {% endfor %}

    <p style="font-size: 11px; color: #aaa; margin-top: 24px; text-align: center; line-height: 1.6;">
      본 메일은 ON-Seoul 서비스 변경 구독에 따라 발송되었습니다.<br>
      &copy; ON-Seoul
    </p>
  </div>
</div>
```

---

## SMS 워크플로우 (`service-change-sms`)

SMS는 본문 길이 제한이 있으므로 `title`/`summary` + 대표 링크만 사용한다.
첫 서비스의 `url`을 대표 링크로 노출한다.

```liquid
[ON-Seoul] {{ data.title }}
{{ data.summary }}
{% if data.services.first.url %}{{ data.services.first.url }}{% endif %}
```

---

## 대시보드 적용 메모

Knock 대시보드 > Workflows에서 `service-change-email` / `service-change-sms` 워크플로우를 만들고,
각 채널 스텝 본문에 위 템플릿을 붙여넣은 뒤 **Commit to environment** 한다.
워크플로우 키는 환경변수(`KNOCK_EMAIL_WORKFLOW_KEY` / `KNOCK_SMS_WORKFLOW_KEY`)와 일치해야 한다.
