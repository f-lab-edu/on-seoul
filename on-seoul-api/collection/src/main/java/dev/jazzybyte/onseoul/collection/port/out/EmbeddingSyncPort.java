package dev.jazzybyte.onseoul.collection.port.out;

import java.util.List;

/**
 * AI 서비스에 임베딩 동기화를 요청한다.
 *
 * <p>변경된 service_id를 upsert/delete로 분류해 AI 서비스
 * {@code POST /embeddings/services/sync} 엔드포인트를 호출한다.
 * 단일 호출은 upsert+delete 합계 ≤ 500 제약을 만족한다고 가정한다(청크 분할은 호출자 책임).
 */
public interface EmbeddingSyncPort {

    /**
     * @param upsert 임베딩 갱신 대상 service_id (비어 있을 수 있음)
     * @param delete 임베딩 삭제 대상 service_id (비어 있을 수 있음)
     * @throws RuntimeException AI 호출 실패 시
     */
    void sync(List<String> upsert, List<String> delete);
}
