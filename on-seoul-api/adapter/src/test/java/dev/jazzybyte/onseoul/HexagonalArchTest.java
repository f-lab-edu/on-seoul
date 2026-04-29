package dev.jazzybyte.onseoul;

import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchRule;

import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

@AnalyzeClasses(packages = "dev.jazzybyte.onseoul")
class HexagonalArchTest {

    @ArchTest
    static final ArchRule domain_must_not_depend_on_frameworks = noClasses()
            .that().resideInAPackage("..domain.model..")
            .or().resideInAPackage("..domain.port..")
            .should().dependOnClassesThat().resideInAnyPackage(
                    "org.springframework..",
                    "jakarta.persistence..",
                    "com.fasterxml..");
    @ArchTest
    static final ArchRule application_must_not_depend_on_adapter = noClasses()
            .that().resideInAPackage("..application..")
            .should().dependOnClassesThat().resideInAPackage("..adapter..");

    @ArchTest
    static final ArchRule inbound_adapter_must_not_depend_on_outbound_adapter = noClasses()
            .that().resideInAPackage("..adapter.in..")
            .should().dependOnClassesThat().resideInAPackage("..adapter.out..");

    @ArchTest
    static final ArchRule collector_must_not_depend_on_domain_model = noClasses()
            .that().resideInAPackage("..collector..")
            .should().dependOnClassesThat().resideInAPackage("..domain.model..")
            .allowEmptyShould(true);

    @ArchTest
    static final ArchRule bootstrap_must_not_depend_on_domain_model = noClasses()
            .that().resideInAPackage("..bootstrap..")
            .should().dependOnClassesThat().resideInAPackage("..domain.model..")
            .allowEmptyShould(true);
}
