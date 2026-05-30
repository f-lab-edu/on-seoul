package dev.jazzybyte.onseoul.crypto;

/**
 * 암호화/복호화 실패 시 던지는 전용 unchecked 예외.
 *
 * <p>AEADBadTagException 등 JCE 하위 예외를 래핑하여 호출 측이
 * crypto 구현 세부사항에 의존하지 않도록 한다.
 */
public class CryptoException extends RuntimeException {

    public CryptoException(String message, Throwable cause) {
        super(message, cause);
    }
}
