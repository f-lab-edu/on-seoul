package dev.jazzybyte.onseoul;

import dev.jazzybyte.onseoul.collection.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveServiceChangeLogPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.collection.port.out.SeoulDatasetFetchPort;
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
        "app.cookie-signing-key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "knock.api-key=test-knock-key",
        "app.encryption.aes-key=0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20",
        "app.encryption.blind-idx-key=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
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
    @MockitoBean LoadSubscriptionPort loadSubscriptionPort;
    @MockitoBean SaveSubscriptionPort saveSubscriptionPort;
    @MockitoBean SaveDispatchPort saveDispatchPort;
    @MockitoBean LoadDispatchPort loadDispatchPort;
    @MockitoBean LoadServiceChangePort loadServiceChangePort;
    @MockitoBean SaveBatchPort saveBatchPort;
    @MockitoBean LoadBatchPort loadBatchPort;
    @MockitoBean SubscriptionFilterParserPort subscriptionFilterParserPort;
    @MockitoBean LoadUserContactPort loadUserContactPort;

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
