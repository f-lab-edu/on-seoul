package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 신규 유저 INSERT를 <b>독립 트랜잭션</b>({@link Propagation#REQUIRES_NEW})으로 격리한다.
 *
 * <p><b>왜 별도 빈 / REQUIRES_NEW 인가</b>: email_hash 유니크 위반(23505)이 발생하면
 * Spring 트랜잭션 매니저가 현재 트랜잭션을 rollback-only로 마킹한다. 만약 이 INSERT가
 * 바깥 {@code socialLogin} 트랜잭션과 같은 경계 안에서 일어나고 그 안에서 예외를 변환해
 * 삼켜버리면, 바깥 트랜잭션 커밋 시점에 {@link org.springframework.transaction.UnexpectedRollbackException}
 * (DataAccessException 서브타입)이 터져 핸들러의 DataAccessException catch로 빠지고
 * {@code email_conflict} 대신 {@code server_error}로 귀결된다.</p>
 *
 * <p>이를 막기 위해 INSERT를 REQUIRES_NEW 로 분리한다. 충돌 시
 * {@link DataIntegrityViolationException}을 <b>변환하지 않고 그대로 전파</b>시켜
 * 이 내부 트랜잭션만 깔끔히 롤백되게 하고(커밋 시도 없음 → UnexpectedRollbackException 없음),
 * 변환은 트랜잭션 경계 밖({@link SocialLoginService})에서 수행한다.</p>
 */
@Component
@RequiredArgsConstructor
class NewUserRegistrar {

    private final SaveUserPort saveUserPort;

    /**
     * 신규 유저를 독립 트랜잭션으로 저장한다.
     *
     * @throws DataIntegrityViolationException email_hash 유니크 위반(경쟁 조건). 변환 없이 전파한다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public User register(SocialLoginCommand command) {
        return saveUserPort.save(User.create(command));
    }
}
