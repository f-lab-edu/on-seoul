package dev.jazzybyte.onseoul.notification.adapter.out.agent;

record TemplateAgentResponse(String title, String body) {
    boolean isValid() {
        return title != null && !title.isBlank() && body != null && !body.isBlank();
    }
}
