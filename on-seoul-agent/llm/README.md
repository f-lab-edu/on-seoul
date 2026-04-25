# llm 모듈

LLM 호출과 임베딩을 추상화하는 모듈입니다. 에이전트와 도구가 LLM·임베딩 API에 직접 의존하지 않도록 단일 진입점을 제공합니다.

**책임 범위**: 
* 프로바이더 전환(`client.py`)
* 텍스트 생성(`generator.py`)
* 벡터 변환(`embedder.py`)

에이전트는 이 모듈만 의존하며, LLM 프로바이더 전환은 `client.py`에 책임을 위임합니다.

---

## 모듈 구조

```
llm/
├── client.py      # LLM 프로바이더 팩토리 + Gemini 임베딩 래퍼 (rate limit · 재시도)
├── generator.py   # 단일 프롬프트 → 텍스트 생성 (동기 래퍼)
└── embedder.py    # 텍스트 → 벡터 변환 (단건 · 배치)
```

---

## 주요 컴포넌트

### client.py — 프로바이더 팩토리 + Gemini 임베딩 래퍼

`get_chat_model()` / `get_embeddings()` 팩토리 함수로 LLM·임베딩 인스턴스를 반환합니다. 프로바이더는 `settings.llm_provider`로 결정되며, `provider` 인자로 런타임에 전환할 수 있습니다.

#### get_chat_model

```python
from llm.client import get_chat_model

llm = get_chat_model()                          # settings.llm_provider 사용
llm = get_chat_model(provider="openai")         # GPT로 전환
llm = get_chat_model(model="gemini-2.0-flash", temperature=0.3)
```

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `provider` | `settings.llm_provider` | `"gemini"` \| `"google"` \| `"openai"` |
| `model` | `settings.gemini_model` / `settings.gpt_model` | 모델 이름 |
| `temperature` | `0.0` | 생성 다양성 |
| `streaming` | `False` | 스트리밍 모드 (OpenAI만 적용) |

#### get_embeddings

```python
from llm.client import get_embeddings

embeddings = get_embeddings()
vector = await embeddings.aembed_query("서울 수영장")
vectors = await embeddings.aembed_documents(["텍스트1", "텍스트2"])
```

Gemini `gemini-embedding-2-preview` 모델을 사용하며 `output_dimensionality=1536`으로 고정합니다. DDL의 `vector(1536)` 컬럼과 일치합니다.

#### Rate Limit 전략 (`_GeminiEmbeddings`)

Gemini Embedding API(무료 티어: RPM 100 / TPM 30K)의 두 가지 한도를 동시에 관리합니다.

| 문제 | 원인 | 해결 |
|---|---|---|
| 버스트 폭발 | `AsyncLimiter(max_rate=N)` 는 버킷이 N 개 토큰으로 꽉 찬 채 시작 → 첫 N 개 요청이 거의 동시에 발사 | `max_rate=1, time_period=60/rpm` 으로 버킷 크기를 1로 고정해 요청 간격을 강제 |
| TPM 스파이크 | 요청 수가 RPM 한도 내여도 문서가 길면 분당 토큰이 순간적으로 초과 | 429 수신 시 지수 백오프(10 s → 20 s → 40 s → 80 s) 후 최대 5회 재시도 |

```
기본 설정 (gemini_embed_rpm=60):
  요청 간격 = 60 / 60 = 1 초
  최대 처리량 = 60 req / min  (무료 티어 100 RPM 대비 40% 여유)
```

limiter는 `_GeminiEmbeddings(base, limiter=...)` 로 주입할 수 있습니다. 테스트에서는 `AsyncLimiter(max_rate=1000, time_period=0.001)` 을 주입해 실제 대기 없이 동작을 검증합니다.

---

### generator.py — 텍스트 생성

단일 프롬프트를 LLM에 전달하고 텍스트 응답을 반환합니다. 에이전트가 직접 LangChain 메시지 타입을 다루지 않아도 되도록 래핑합니다.

```python
from llm.generator import Generator

gen = Generator()
answer = await gen.generate(
    prompt="강남구 체육 시설 알려줘",
    system="당신은 서울시 공공서비스 안내 전문가입니다.",
)
```

| 파라미터 | 설명 |
|---|---|
| `prompt` | 사용자 입력 또는 에이전트가 조립한 프롬프트 |
| `system` | 시스템 프롬프트 (생략 가능) |

LLM 호출 실패 시 `LLMException`으로 래핑하여 상위로 전달합니다.

---

### embedder.py — 벡터 변환

텍스트를 `get_embeddings()` 로 가져온 임베딩 인스턴스에 위임합니다. 단건(`embed`)과 배치(`embed_many`) 두 가지 인터페이스를 제공합니다.

```python
from llm.embedder import Embedder

emb = Embedder()
vector = await emb.embed("서울 수영장")                        # list[float] (len=1536)
vectors = await emb.embed_many(["텍스트1", "텍스트2"])         # list[list[float]]
```

`embed_many`는 내부적으로 `aembed_documents` → 순차 처리 → rate limit 적용 경로를 거칩니다. 임베딩 실패 시 `LLMException`으로 래핑합니다.

---

## 설정

`core/config.py` (pydantic-settings) 로 관리합니다. `.env` 또는 환경변수로 재정의합니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `"gemini"` | `"gemini"` \| `"openai"` |
| `GOOGLE_API_KEY` | — | Gemini 사용 시 필수 |
| `OPENAI_API_KEY` | — | OpenAI 사용 시 필수 |
| `GEMINI_MODEL` | `"gemini-2.0-flash"` | Chat 모델 이름 |
| `GPT_MODEL` | `"gpt-4o-mini"` | OpenAI Chat 모델 이름 |
| `EMBEDDING_MODEL` | `"models/gemini-embedding-2-preview"` | 임베딩 모델 |
| `GEMINI_EMBED_RPM` | `60` | Gemini 임베딩 RPM 한도. 무료 티어 안전값: 60 이하 |

---

## 예외

모든 LLM·임베딩 호출 실패는 `core.exceptions.LLMException` 으로 래핑됩니다. 에이전트는 이 예외 하나만 처리하면 됩니다.

```python
from core.exceptions import LLMException

try:
    answer = await gen.generate(prompt)
except LLMException as e:
    # e.detail 에 원본 예외 포함
    ...
```

`ConfigurationException` 은 API 키 미설정 등 초기화 오류 시 `get_chat_model()` / `get_embeddings()` 에서 즉시 발생합니다.
