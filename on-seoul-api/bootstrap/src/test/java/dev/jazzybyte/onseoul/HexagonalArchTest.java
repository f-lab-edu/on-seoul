package dev.jazzybyte.onseoul;

import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchRule;

import static com.tngtech.archunit.core.domain.JavaClass.Predicates.resideInAnyPackage;
import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

@AnalyzeClasses(packages = "dev.jazzybyte.onseoul")
class HexagonalArchTest {

    /**
     * BC domain 패키지 (예: user.domain, chat.domain, collection.domain, notification.domain)는
     * Spring, JPA, Jackson 프레임워크에 의존하면 안 된다.
     */
    @ArchTest
    static final ArchRule domain_must_not_depend_on_frameworks = noClasses()
            .that().resideInAPackage("..user.domain..")
            .or().resideInAPackage("..chat.domain..")
            .or().resideInAPackage("..collection.domain..")
            .or().resideInAPackage("..notification.domain..")
            .or().resideInAPackage("..user.port..")
            .or().resideInAPackage("..chat.port..")
            .or().resideInAPackage("..collection.port..")
            .or().resideInAPackage("..notification.port..")
            .should().dependOnClassesThat().resideInAnyPackage(
                    "org.springframework..",
                    "jakarta.persistence..",
                    "com.fasterxml..");

    /**
     * BC application 패키지는 adapter 패키지에 의존하면 안 된다.
     */
    @ArchTest
    static final ArchRule application_must_not_depend_on_adapter = noClasses()
            .that().resideInAPackage("..user.application..")
            .or().resideInAPackage("..chat.application..")
            .or().resideInAPackage("..collection.application..")
            .or().resideInAPackage("..notification.application..")
            .should().dependOnClassesThat().resideInAPackage("..adapter..");

    /**
     * BC inbound adapter는 outbound adapter에 의존하면 안 된다.
     */
    @ArchTest
    static final ArchRule inbound_adapter_must_not_depend_on_outbound_adapter = noClasses()
            .that().resideInAPackage("..adapter.in..")
            .should().dependOnClassesThat().resideInAPackage("..adapter.out..");

    /**
     * ADR-0001: BC 간 통신은 inbound port(use case) 인터페이스를 통해서만 한다.
     * chat·collection·notification의 domain/application 클래스는 user.domain 클래스를 직접 import하면 안 된다.
     * (user_id 같은 원시값 전달은 허용되므로 user.domain.* 클래스 참조만 금지)
     */
    @ArchTest
    static final ArchRule bc_chat_must_not_import_user_domain = noClasses()
            .that().resideInAPackage("..chat.domain..")
            .or().resideInAPackage("..chat.application..")
            .should().dependOnClassesThat().resideInAPackage("..user.domain..");

    @ArchTest
    static final ArchRule bc_collection_must_not_import_user_domain = noClasses()
            .that().resideInAPackage("..collection.domain..")
            .or().resideInAPackage("..collection.application..")
            .should().dependOnClassesThat().resideInAPackage("..user.domain..");

    @ArchTest
    static final ArchRule bc_notification_must_not_import_user_domain = noClasses()
            .that().resideInAPackage("..notification.domain..")
            .or().resideInAPackage("..notification.application..")
            .should().dependOnClassesThat().resideInAPackage("..user.domain..");

    /**
     * ADR-0001: chat.adapter는 다른 BC의 port를 직접 구현하면 안 된다.
     * M1 같은 위반(chat 어댑터가 collection.port를 구현)을 사전에 차단한다.
     */
    @ArchTest
    static final ArchRule chat_adapter_must_not_implement_other_bc_ports = noClasses()
            .that().resideInAPackage("..chat.adapter..")
            .should().implement(resideInAnyPackage(
                    "..collection.port..",
                    "..user.port..",
                    "..notification.port.."));

    /**
     * ADR-0001: collection.adapter는 다른 BC의 port를 직접 구현하면 안 된다.
     */
    @ArchTest
    static final ArchRule collection_adapter_must_not_implement_other_bc_ports = noClasses()
            .that().resideInAPackage("..collection.adapter..")
            .should().implement(resideInAnyPackage(
                    "..chat.port..",
                    "..user.port..",
                    "..notification.port.."));
}
