package dev.jazzybyte.onseoul;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication(scanBasePackages = "dev.jazzybyte.onseoul")
public class OnSeoulApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(OnSeoulApiApplication.class, args);
    }
}
