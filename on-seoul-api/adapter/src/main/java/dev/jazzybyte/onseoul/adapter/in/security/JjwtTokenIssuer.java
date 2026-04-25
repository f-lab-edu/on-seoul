package dev.jazzybyte.onseoul.adapter.in.security;

import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import io.jsonwebtoken.*;
import io.jsonwebtoken.security.Keys;
import io.jsonwebtoken.security.SignatureException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.util.Base64;
import java.util.Date;
import java.util.Optional;

@Component
public class JjwtTokenIssuer implements TokenIssuerPort {

    private final SecretKey signingKey;
    private final long accessTokenMinutes;
    private final long refreshTokenMinutes;

    public JjwtTokenIssuer(
            @Value("${jwt.secret}") String base64Secret,
            @Value("${jwt.access-token-minutes:15}") long accessTokenMinutes,
            @Value("${jwt.refresh-token-minutes:10080}") long refreshTokenMinutes
    ) {
        byte[] keyBytes = Base64.getDecoder().decode(base64Secret);
        this.signingKey = Keys.hmacShaKeyFor(keyBytes);
        this.accessTokenMinutes = accessTokenMinutes;
        this.refreshTokenMinutes = refreshTokenMinutes;
    }

    @Override
    public String generateAccessToken(long userId) {
        return buildToken(userId, accessTokenMinutes);
    }

    @Override
    public String generateRefreshToken(long userId) {
        return buildToken(userId, refreshTokenMinutes);
    }

    private String buildToken(long userId, long ttlMinutes) {
        Date now = new Date();
        Date expiry = new Date(now.getTime() + ttlMinutes * 60_000L);
        return Jwts.builder()
                .subject(String.valueOf(userId))
                .issuedAt(now)
                .expiration(expiry)
                .signWith(signingKey)
                .compact();
    }

    @Override
    public void validateToken(String token) {
        parseToken(token);
    }

    @Override
    public Long extractUserId(String token) {
        Claims claims = parseToken(token);
        return Long.parseLong(claims.getSubject());
    }

    @Override
    public Optional<Long> extractUserIdSafely(String token) {
        try {
            Claims claims = parseToken(token);
            return Optional.of(Long.parseLong(claims.getSubject()));
        } catch (OnSeoulApiException e) {
            return Optional.empty();
        }
    }

    private Claims parseToken(String token) {
        try {
            return Jwts.parser()
                    .verifyWith(signingKey)
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
        } catch (ExpiredJwtException e) {
            throw new OnSeoulApiException(ErrorCode.EXPIRED_TOKEN, "만료된 토큰입니다.", e);
        } catch (SignatureException | MalformedJwtException | UnsupportedJwtException | IllegalArgumentException e) {
            throw new OnSeoulApiException(ErrorCode.INVALID_TOKEN, "유효하지 않은 토큰입니다.", e);
        }
    }
}
