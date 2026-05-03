package dev.jazzybyte.onseoul.collector;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication(scanBasePackages = {
    "dev.jazzybyte.onseoul.collector",
    "dev.jazzybyte.onseoul.adapter",
    "dev.jazzybyte.onseoul.application"
})
@EnableScheduling
public class CollectorApplication {

    public static void main(String[] args) {
        SpringApplication.run(CollectorApplication.class, args);
    }
}
