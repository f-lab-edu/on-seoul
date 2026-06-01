package dev.jazzybyte.onseoul.notification.adapter.out.agent;

record TemplateAgentResponse(String title, String summary) {
    boolean isValid() {
        return title != null && !title.isBlank() && summary != null && !summary.isBlank();
    }
}
