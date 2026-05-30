package dev.jazzybyte.onseoul.notification.domain;

/**
 * 알림 발송에 필요한 수신자 연락처 정보.
 * user BC를 직접 import하지 않기 위한 notification BC 전용 VO.
 *
 * @param userId      on-seoul 사용자 ID — 필수. Knock 수신자 식별자로 사용됨
 * @param email       이메일 주소. null이면 EMAIL 채널 발송 불가
 * @param phoneNumber 전화번호 (E.164 형식). null이면 SMS 채널 발송 불가
 */
public record UserContact(Long userId, String email, String phoneNumber) {

    public UserContact {
        if (userId == null) {
            throw new IllegalArgumentException("UserContact.userId는 필수입니다");
        }
    }
}
