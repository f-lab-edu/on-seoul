package dev.jazzybyte.onseoul;

import dev.jazzybyte.onseoul.domain.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveServiceChangeLogPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.domain.port.out.SeoulDatasetFetchPort;
import lombok.extern.slf4j.Slf4j;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

@Slf4j
@SpringBootTest
@TestPropertySource(properties = {
        "spring.autoconfigure.exclude=" +
        "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration," +
        "org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration," +
        "org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration," +
        "org.springframework.boot.autoconfigure.data.redis.RedisReactiveAutoConfiguration",
        "jwt.secret=dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
        "spring.security.oauth2.client.registration.google.client-id=test",
        "spring.security.oauth2.client.registration.google.client-secret=test",
        "spring.security.oauth2.client.registration.google.scope=openid,email,profile",
        "seoul.api.key=test",
        "kakao.api.key=test",
        "ai.service.url=http://localhost:8000",
        "ai.service.stream-timeout-seconds=120",
        "app.cookie-signing-key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
})
class OnSeoulApiApplicationTests {

    @MockitoBean LoadUserPort loadUserPort;
    @MockitoBean SaveUserPort saveUserPort;
    @MockitoBean RefreshTokenStorePort refreshTokenStorePort;
    @MockitoBean LoadPublicServicePort loadPublicServicePort;
    @MockitoBean SavePublicServicePort savePublicServicePort;
    @MockitoBean LoadApiSourceCatalogPort loadApiSourceCatalogPort;
    @MockitoBean SaveCollectionHistoryPort saveCollectionHistoryPort;
    @MockitoBean SaveServiceChangeLogPort saveServiceChangeLogPort;
    @MockitoBean SeoulDatasetFetchPort seoulDatasetFetchPort;
    @MockitoBean GeocodingPort geocodingPort;
    @MockitoBean StringRedisTemplate stringRedisTemplate;
    @MockitoBean SaveChatRoomPort saveChatRoomPort;
    @MockitoBean LoadChatRoomPort loadChatRoomPort;
    @MockitoBean SaveChatMessagePort saveChatMessagePort;

    @Test
    void contextLoads(ApplicationContext applicationContext) {
        String[] beanNames = applicationContext.getBeanDefinitionNames();
        long onSeoulBeans = 0;
        for (String beanName : beanNames) {
            Object bean = applicationContext.getBean(beanName);
            String packageName = bean.getClass().getPackage() != null
                    ? bean.getClass().getPackage().getName()
                    : "";
            if (packageName.startsWith("dev.jazzybyte.onseoul")) {
                log.info("Bean: {} ({})", beanName, packageName);
                onSeoulBeans++;
            }
        }
        log.info("Total dev.jazzybyte.onseoul beans: {}", onSeoulBeans);
    }
}
