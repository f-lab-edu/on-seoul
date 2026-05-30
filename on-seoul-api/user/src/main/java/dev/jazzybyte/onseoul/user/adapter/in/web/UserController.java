package dev.jazzybyte.onseoul.user.adapter.in.web;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.user.port.in.UpdateContactUseCase;
import dev.jazzybyte.onseoul.user.port.in.UpdateContactUseCase.UpdateContactCommand;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Size;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/users")
@RequiredArgsConstructor
public class UserController {

    private final UpdateContactUseCase updateContactUseCase;

    /**
     * 내 연락처(전화번호) 수정.
     * JWT 인증 필요 — JwtAuthenticationFilter가 request attribute "userId"를 설정한다.
     */
    @PatchMapping("/me/contact")
    public ResponseEntity<Void> updateContact(
            @RequestAttribute(required = false) Long userId,
            @Valid @RequestBody UpdateContactRequest request) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
        updateContactUseCase.updateContact(userId, new UpdateContactCommand(request.phoneNumber()));
        return ResponseEntity.ok().build();
    }

    record UpdateContactRequest(@Size(max = 20) String phoneNumber) {}
}
