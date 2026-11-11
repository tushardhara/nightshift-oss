package com.example.dogfoodb;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GreetingControllerTest {

    @Test
    void greetingReturnsExpectedText() {
        GreetingController controller = new GreetingController();
        assertEquals("hello from dogfood b", controller.greeting());
    }
}
