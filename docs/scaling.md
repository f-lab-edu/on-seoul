# on-seoul 수평 확장 가이드 (제안 3: 1000 QPS)

## 목표

인스턴스당 200 QPS 기준, **최대 5 인스턴스 = 1000 QPS** 집계 처리 (스케일 범위 1~5 인스턴스).

---

## 커넥션 수 분석

| 구성 요소 | 유저 | 인스턴스 | 풀 cap/인스턴스 | 총 클라이언트 커넥션 |
|----------|------|----------|------------|-------------------|
| on-seoul-agent (AI) | on_ai_app | 5 | 50 (pool_size 20 + max_overflow 30) | 250 |
| on-seoul-api (Data) | on_data_app | 5 | 30 (pool_size 10 + max_overflow 20) | 150 |
| 합계 | | | | **400** |

> 풀 cap = SQLAlchemy 엔진별 `pool_size + max_overflow` (on-seoul-agent `core/database.py`).
> 인스턴스 수는 1~5 범위에서 스케일하며, 5 인스턴스 만재 시 합계 400 으로 동일하다.

PostgreSQL 기본 `max_connections = 100` 으로는 400 커넥션을 수용할 수 없다.
PgBouncer(transaction mode)로 400 클라이언트 커넥션을 PG 백엔드 ~120개로 압축한다.

---

## PgBouncer 풀 설계

```
clients (400)
    │
    ▼
PgBouncer (max_client_conn=600)
    ├── on_ai  pool  (pool_size=80)  ──► PostgreSQL on_ai  DB
    └── on_data pool (pool_size=40)  ──► PostgreSQL on_data DB
                                          총 서버 커넥션 ≤ 120
```

- `pool_mode = transaction`: 각 SQL 트랜잭션 완료 후 커넥션 반환.
- SQLAlchemy asyncpg 는 반드시 `statement_cache_size=0` 으로 설정해야 한다
  (prepared statement는 세션-고정을 요구하므로 transaction mode 와 충돌).
  on-seoul-agent `core/database.py` 에 이미 적용됨 (on_ai/on_data 두 엔진 모두).

> **숫자 source-of-truth**: 위 풀 사이징 상세 값(`pool_size` 80/40, `max_client_conn` 600,
> 산정 근거)은 `infra/pgbouncer/pgbouncer.ini` 와 `docker-compose-pgbouncer.yml` 주석이
> 단일 진실 원천이다. 본 문서는 요약 수치(집계 400 / on_ai 풀 80 · on_data 풀 40 /
> PG max_connections 200)와 설계 의도·오케스트레이션만 서술하며, 값을 바꿀 때는
> `.ini` 를 먼저 갱신하고 본 문서의 요약 수치를 맞춘다(드리프트 방지).

### PG max_connections 설정

자체 호스팅 PostgreSQL:

```sql
ALTER SYSTEM SET max_connections = 200;
SELECT pg_reload_conf();
-- 재시작 필요 여부 확인:
SHOW max_connections;
```

관리형 PostgreSQL(RDS, AlloyDB, Cloud SQL 등):
- **AWS RDS**: 파라미터 그룹 > `max_connections` 수정 후 적용 (재시작 필요).
- **GCP Cloud SQL**: 데이터베이스 플래그 > `max_connections` 수정.
- **Azure Database**: 서버 파라미터 > `max_connections` 수정.

max_connections = 200 요약: PgBouncer 서버 풀 합계 80 + 40 = 120, 여기에 DBA/모니터링/
비상 슬롯 및 안전 마진을 더해 200. 상세 산정 근거는 `docker-compose-pgbouncer.yml` 주석을
단일 진실 원천으로 한다(중복 서술 방지).

---

## 레플리카 수평 확장

### Docker Swarm

```bash
# 초기화 (단일 노드 swarm)
docker swarm init

# 스택 배포
docker stack deploy -c docker-compose-app.yml on-seoul

# 레플리카 5개로 스케일업 (현행 capacity 모델 상한)
docker service scale on-seoul_agent=5
```

`docker-compose-app.yml` deploy 블록 예시 (Swarm 모드 전용):

```yaml
deploy:
  replicas: 5  # 1~5 범위. 상한 5는 PgBouncer 풀(on_ai 80/on_data 40)과 함께 산정된 값
  update_config:
    parallelism: 2
    delay: 10s
  restart_policy:
    condition: on-failure
    max_attempts: 3
```

### Kubernetes + HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: on-seoul-agent-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: on-seoul-agent
  minReplicas: 2
  # maxReplicas 는 현행 capacity 모델 상한(5 인스턴스)에 맞춘다.
  # 5 를 초과해 늘리려면 PgBouncer 풀(on_ai 80/on_data 40)과 PG max_connections(200)를
  # 함께 재산정해야 한다 — pgbouncer.ini 가 source-of-truth.
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
```

---

## L4 로드 밸런싱

### AWS ALB

1. Target Group: `on-seoul-agent` 인스턴스/컨테이너, 포트 8000.
2. Health Check: `GET /health` → HTTP 200.
3. 알고리즘: Least Outstanding Requests (SSE 스트림 수 균등 분산).

### Nginx upstream (자체 호스팅)

```nginx
upstream agent_upstream {
    least_conn;  # SSE 스트림 수 고려 시 least_conn 권장
    server agent_1:8000;
    server agent_2:8000;
    # ... 최대 5개 (현행 capacity 모델 상한)
    keepalive 64;
}
```

### Docker Swarm 내장 DNS round-robin

Swarm 모드에서는 서비스 이름 하나로 자동 round-robin 된다:

```nginx
upstream agent_upstream {
    server agent:8000;  # Swarm이 태스크(최대 5개)로 분산
    keepalive 64;
}
```

---

## SSE Sticky Session 불필요 이유

SSE(Server-Sent Events) 스트림은 **단일 TCP 연결의 수명** 내에서만 유지된다.

- 스트림 중 요청을 다른 레플리카로 이전할 일이 없다 (한 번 연결되면 끝까지 유지).
- 대화 이력(`conversation`, `message` 테이블)은 PostgreSQL 공유 스토리지에 저장되므로
  어느 레플리카가 다음 요청을 처리해도 이전 맥락을 읽을 수 있다.
- 따라서 sticky session(IP hash, cookie 기반) 없이 round-robin / least_conn 으로 충분하다.

---

## 개발 환경 vs 운영 환경

| 항목 | 개발 (로컬) | 운영 |
|------|------------|------|
| agent 레플리카 | 1 | 2~5 (HPA/Swarm) |
| PgBouncer | docker-compose-pgbouncer.yml | 동일 또는 관리형 Pgbouncer(RDS Proxy 등) |
| Nginx | nginx/conf.d/default.conf | 동일 또는 AWS ALB |
| `ulimits nofile` | 10000 | 10000 (또는 OS limit 조정) |

개발 환경에서는 `docker-compose-app.yml` 의 `deploy.replicas` 블록을 주석 처리한 채 단일 인스턴스로 기동한다.

---

## 운영 환경 주의 사항

### PgBouncer 포트 노출 제거

`docker-compose-pgbouncer.yml` 의 `ports` 항목(`127.0.0.1:5433:5432`)은 로컬 개발 디버깅 전용이다.
운영 환경에서는 해당 항목을 제거한다. 앱 컨테이너는 내부 네트워크(`on-seoul-net`)의
서비스 DNS `pgbouncer:5432` 로만 접근하므로 호스트 포트 바인딩이 불필요하다.

### on-seoul-agent uvicorn 단일 프로세스 유지

on-seoul-agent 컨테이너는 **uvicorn 단일 프로세스**(`--workers 1`)로 운영해야 한다.

`core/concurrency.py` 의 글로벌 세마포어(`vector_global_concurrency=40`, `asyncio.Semaphore`)는
**프로세스 내부**에만 존재한다. `--workers N` (N > 1) 으로 멀티워커를 구동하면 세마포어가
프로세스당 독립적으로 생성되어 워커마다 상한이 독립 적용된다. 즉 한 인스턴스에서 W 워커를 띄우면
글로벌 동시 상한이 의도한 40 이 아니라 실질 W×40 으로, 커넥션 점유도 인스턴스 풀 cap(on_ai 50)을
넘어 W 배로 부풀 수 있다. 5 인스턴스 × 멀티워커 조합이면 PgBouncer on_ai 풀(pool_size=80)을
쉽게 초과한다.

수평 확장은 워커 수 증가 대신 **컨테이너 레플리카** 수를 늘리는 방식으로 수행한다
(Swarm `replicas`, Kubernetes HPA). 레플리카당 세마포어 상한이 독립 적용되므로
PgBouncer 풀 크기와 레플리카 수를 함께 조정해야 한다.
