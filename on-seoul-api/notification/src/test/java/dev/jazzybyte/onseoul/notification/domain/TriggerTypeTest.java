package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class TriggerTypeTest {

    @Test
    @DisplayName("enum 이름은 DB CHECK 제약(chk_nd_trigger_type) 화이트리스트와 정확히 일치한다")
    void names_matchDbWhitelist() {
        assertThat(TriggerType.values())
                .extracting(Enum::name)
                .containsExactly("CHANGE", "OPEN_DAY", "BEFORE_RECEIPT_D1", "DEADLINE_DDAY");
    }

    @Test
    @DisplayName("CHANGE는 1순위, 시점 3종은 2순위")
    void priority_changeFirst_scheduledSecond() {
        assertThat(TriggerType.CHANGE.priority()).isEqualTo(1);
        assertThat(TriggerType.OPEN_DAY.priority()).isEqualTo(2);
        assertThat(TriggerType.BEFORE_RECEIPT_D1.priority()).isEqualTo(2);
        assertThat(TriggerType.DEADLINE_DDAY.priority()).isEqualTo(2);
    }

    @Test
    @DisplayName("isChange / isScheduled 분류가 일관된다")
    void classification_isConsistent() {
        assertThat(TriggerType.CHANGE.isChange()).isTrue();
        assertThat(TriggerType.CHANGE.isScheduled()).isFalse();
        for (TriggerType t : new TriggerType[]{
                TriggerType.OPEN_DAY, TriggerType.BEFORE_RECEIPT_D1, TriggerType.DEADLINE_DDAY}) {
            assertThat(t.isChange()).isFalse();
            assertThat(t.isScheduled()).isTrue();
        }
    }
}
