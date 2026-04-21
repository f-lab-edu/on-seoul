package dev.jazzybyte.onseoul;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class OnSeoulApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(OnSeoulApiApplication.class, args);
    }

}
