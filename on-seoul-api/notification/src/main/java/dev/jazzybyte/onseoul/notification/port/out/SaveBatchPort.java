package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;

public interface SaveBatchPort {

    /** 신규 RUNNING 배치를 INSERT 하고 id/startedAt이 채워진 배치를 반환한다. */
    NotificationBatch insertRunning(NotificationBatch batch);

    /** 종료 시 status/finishedAt/sentCount/failedCount를 갱신한다. */
    NotificationBatch update(NotificationBatch batch);
}
