package dev.jazzybyte.onseoul.collector.dto;

/**
 * 단일 소스 배치 upsert 결과.
 * deleted는 UpsertService 책임 밖(CollectionService의 deletion sweep에서 채운다).
 */
public record UpsertResult(int newCount, int updatedCount, int unchangedCount) {

    public static UpsertResult empty() {
        return new UpsertResult(0, 0, 0);
    }
}
