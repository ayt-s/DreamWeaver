package com.dreamweaver;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
@MapperScan("com.dreamweaver.mapper")
public class DreamWeaverApplication {

    public static void main(String[] args) {
        SpringApplication.run(DreamWeaverApplication.class, args);
    }
}