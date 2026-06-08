"""W1 참조 해소 규칙 단위 테스트 (agents/_reference_resolution.py)."""

from agents._reference_resolution import resolve_reference

_ENTS = [
    {"service_id": "S1", "label": "마루공원 테니스장"},
    {"service_id": "S2", "label": "강남구민체육센터 수영장"},
    {"service_id": "S3", "label": "마포 청소년 코딩 강좌"},
]


class TestGate:
    def test_empty_prev_entities_is_non_referential(self):
        assert resolve_reference("이 곳이 어떤 곳이야?", []) == []
        assert resolve_reference("이 곳이 어떤 곳이야?", None) == []

    def test_non_referential_new_query(self):
        # 화제 전환 — 지시어/서수/라벨 없음 → 과잉 carryover 안 함
        assert resolve_reference("강남구 풋살장 알려줘", _ENTS) == []


class TestDemonstratives:
    def test_demonstrative_binds_first_entity(self):
        assert resolve_reference("이 곳이 어떤 곳이야?", _ENTS) == ["S1"]

    def test_banggeum_geugeo(self):
        assert resolve_reference("방금 그거 다시 설명해줘", _ENTS) == ["S1"]


class TestOrdinals:
    def test_korean_ordinal(self):
        assert resolve_reference("세번째 알려줘", _ENTS) == ["S3"]

    def test_first(self):
        assert resolve_reference("첫번째 곳 자세히", _ENTS) == ["S1"]

    def test_numeric_ordinal(self):
        assert resolve_reference("2번째 알려줘", _ENTS) == ["S2"]

    def test_beon(self):
        assert resolve_reference("1번 어떤 곳이야", _ENTS) == ["S1"]

    def test_out_of_range_ordinal_ignored(self):
        # 9번째는 범위 밖 → 서수 매칭 없음 → 다른 신호 없으면 []
        assert resolve_reference("아홉번째", _ENTS) == []

    def test_pure_beon_without_cue_is_not_ordinal(self):
        # SHOULD-FIX 1: 째 없는 순수 "N번"은 참조 단서가 없으면 서수로 보지 않는다.
        # "3번 출구"(지하철 출구), "10번 버스"(버스 번호)는 비-서수 N번.
        assert resolve_reference("3번 출구 근처 시설 알려줘", _ENTS) == []
        assert resolve_reference("10번 버스 타는 곳", _ENTS) == []

    def test_pure_beon_with_cue_is_ordinal(self):
        # 참조 단서("비교"/"어때"/"어떤")가 함께 있으면 순수 "N번"도 서수로 채택.
        assert resolve_reference("1번이랑 2번 비교", _ENTS) == ["S1", "S2"]
        assert resolve_reference("1번 어때", _ENTS) == ["S1"]

    def test_beonjjae_always_ordinal_even_without_cue(self):
        # 째가 명시된 "N번째"는 단서 없이도 항상 서수.
        assert resolve_reference("2번째", _ENTS) == ["S2"]


class TestLabelPartialMatch:
    def test_label_partial_match(self):
        assert resolve_reference("마루공원 그거 어떤 곳이야?", _ENTS) == ["S1"]

    def test_generic_token_not_matched_alone(self):
        # "테니스장"만으로는 과잉 매칭하지 않는다(일반 카테고리 토큰)
        assert resolve_reference("테니스장 더 추천해줘", _ENTS) == []

    def test_full_label_substring_match(self):
        # QA 갭: 라벨 전체(공백 제거)가 메시지에 그대로 포함되면 매칭(서수 분기 우선
        # 아님 — 라벨 분기). 사용자가 직전 시설명을 그대로 다시 언급하는 경우.
        assert resolve_reference("강남구민체육센터 수영장 예약돼?", _ENTS) == ["S2"]

    def test_topic_switch_shared_region_prefix_not_referential(self):
        # MUST-FIX: 직전 라벨 "마포 청소년 코딩 강좌"(S3)와 자치구 prefix "마포"만
        # 공유하는 화제 전환 질의 "마포 수영장 알려줘"는 referential 이 아니다.
        # "마포" 단일 토큰 부분일치 + 라벨에 없는 일반 카테고리 토큰("수영장") 도입
        # → 새 검색으로 간주하고 carryover 하지 않는다.
        assert resolve_reference("마포 수영장 알려줘", _ENTS) == []

    def test_legitimate_label_reference_still_binds(self):
        # MUST-FIX(역방향): 직전 라벨을 정당하게 재언급하는 질의는 여전히 바인딩.
        # "마포 청소년 코딩 강좌 그거 어때" → 라벨 전체 포함 → S3.
        assert resolve_reference("마포 청소년 코딩 강좌 그거 어때", _ENTS) == ["S3"]

    def test_multi_distinctive_token_match_binds(self):
        # 변별 토큰 2개 이상 동시 일치는 강한 신호 → 바인딩(일반 토큰 도입 무관).
        assert resolve_reference("마포 코딩 수업 어때", _ENTS) == ["S3"]


class TestMultiReference:
    def test_multi_ordinal_binding(self):
        assert resolve_reference("1번이랑 3번 비교해줘", _ENTS) == ["S1", "S3"]

    def test_multi_ordinal_order_normalized_ascending(self):
        # QA 갭: 메시지 등장 순서가 역순("3번...1번")이어도 prev_entities 인덱스
        # 오름차순으로 정규화한다(설계 계약: _ordinal_indices 의 sorted(set(...))).
        # 바인딩 순서가 prev_entities 순서를 보존함을 핀으로 고정한다.
        assert resolve_reference("3번이랑 1번 비교", _ENTS) == ["S1", "S3"]

    def test_multi_ordinal_mixed_in_and_out_of_range(self):
        # QA 갭: 다중 서수 중 일부만 범위 안("1번"=유효, "다섯번째"=범위 밖, 3건뿐).
        # 범위 밖은 무시하고 유효한 것만 바인딩한다(과잉/IndexError 없음).
        assert resolve_reference("1번이랑 다섯번째 비교", _ENTS) == ["S1"]


class TestEdgeCases:
    def test_duplicate_service_id_preserved(self):
        # QA 갭: prev_entities 에 동일 service_id 가 중복 존재(서로 다른 라벨)할 때
        # 서수 다중 참조는 각 인덱스의 service_id 를 그대로 반환한다(중복 제거하지 않음).
        # 중복 제거 책임은 하류(hydrate_services)에 둔다 — 해소 계층은 인덱스 충실.
        dup = [
            {"service_id": "S1", "label": "A 테니스장"},
            {"service_id": "S1", "label": "A 테니스장 별관"},
        ]
        assert resolve_reference("1번이랑 2번 비교", dup) == ["S1", "S1"]

    def test_demonstrative_with_multi_entities_binds_first_only(self):
        # QA 갭: 지시어("이거")는 특정 엔티티 미지정이므로 다건이어도 첫 엔티티만 바인딩.
        assert resolve_reference("이거 어때", _ENTS) == ["S1"]

    def test_generic_label_token_substring_does_not_overmatch(self):
        # QA 갭: 라벨의 일반 카테고리 토큰("수영장")이 메시지에 등장해도 단독으로는
        # 매칭하지 않아 과잉 carryover 를 막는다(라벨 비-일반 토큰만 매칭 신호).
        assert resolve_reference("수영장 더 보여줘", _ENTS) == []

    def test_distinctive_label_token_binds_all_sharing_entities(self):
        # QA 갭: 변별력 있는 라벨 토큰("마루공원")이 두 엔티티 라벨에 공통이면 둘 다 바인딩.
        # (의도된 동작 — 같은 변별 토큰을 공유하는 후보를 모두 대상으로 삼음)
        shared = [
            {"service_id": "S1", "label": "노원 마루공원"},
            {"service_id": "S2", "label": "마루공원 별관"},
        ]
        assert resolve_reference("마루공원 어때", shared) == ["S1", "S2"]
