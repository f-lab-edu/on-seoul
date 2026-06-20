"""프로세스 전역 동시성 제어 객체.

글로벌 VECTOR fan-out 세마포어를 보관한다.

설계 근거 — app.state vs. 모듈 전역:
    VectorAgent는 AgentGraph.__init__ 시점에 생성되며, FastAPI Request 객체나
    app.state를 그래프 노드 체인까지 전파하면 의존성 역전이 발생한다.
    단일 asyncio 이벤트 루프(uvicorn 단일 프로세스)이므로 모듈 전역 변수가
    안전하며 테스트에서도 직접 교체 가능하다.

    asyncio.Semaphore는 이벤트 루프 생성 이후에 만들어야 하므로
    모듈 임포트 시점이 아닌 lifespan(main.py)에서 init_global_sema()를 호출한다.

경고 — 멀티 프로세스 배포 금지:
    이 모듈의 세마포어는 단일 asyncio 이벤트 루프(uvicorn 단일 프로세스)를 전제한다.
    `uvicorn --workers N` 또는 gunicorn 멀티워커로 실행하면 vector_global_sema가
    프로세스마다 독립 인스턴스로 생성되어 글로벌 cap 역할을 하지 못한다.
    결과적으로 총 DB 연결 상한이 N배가 되어 PostgreSQL 연결 풀이 고갈될 수 있다.
    컨테이너당 반드시 uvicorn 단일 프로세스로 실행하고, 수평 확장은 컨테이너 복제로 수행한다.
"""

import asyncio

from core.config import settings

# None: lifespan 이전(테스트·임포트 시점). lifespan에서 init_global_sema() 호출 후 채워진다.
vector_global_sema: asyncio.Semaphore | None = None


def init_global_sema(concurrency: int | None = None) -> asyncio.Semaphore:
    """이벤트 루프 시작 이후 호출하여 글로벌 세마포어를 초기화한다.

    Parameters
    ----------
    concurrency:
        None이면 settings.vector_global_concurrency(기본 40)를 사용한다.

    Returns
    -------
    asyncio.Semaphore
        초기화된 세마포어. main.py lifespan에서 반환값을 사용하지 않아도 된다.
    """
    global vector_global_sema  # noqa: PLW0603
    cap = concurrency if concurrency is not None else settings.vector_global_concurrency
    vector_global_sema = asyncio.Semaphore(cap)
    return vector_global_sema
