"use client";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";

export default function ChatPlaceholderPage() {
  const { user, loading, logout } = useAuth();

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-4">
      <h1 className="text-xl font-medium">채팅 페이지 준비 중</h1>
      <p className="text-sm text-muted-foreground">
        {loading ? "사용자 정보를 불러오는 중…" : user ? `${user.nickname}님 환영합니다.` : ""}
      </p>
      <Button variant="outline" onClick={() => void logout()}>
        로그아웃
      </Button>
    </main>
  );
}
