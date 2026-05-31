package dev.jazzybyte.onseoul;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication(scanBasePackages = "dev.jazzybyte.onseoul")
@EnableScheduling
@EnableAsync
public class OnSeoulApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(OnSeoulApiApplication.class, args);
    }
}
