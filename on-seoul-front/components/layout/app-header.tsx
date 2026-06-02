"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { LogOut, Menu, MessageSquarePlus, Bell } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

interface AppHeaderProps {
  /** 레거시 override. 미지정 시 현재 경로의 NAV_ITEMS pageTitle로 자동 결정. */
  title?: string;
}

type NavItem = {
  href: string;
  label: string;
  /** 페이지 헤더에 노출할 타이틀. nav 라벨과 다를 때만 명시. */
  pageTitle?: string;
  icon: typeof Menu;
  /** 정확 매칭 강제 (`/`처럼 prefix 매칭이 위험한 경로용). */
  exact?: boolean;
};

const NAV_ITEMS: ReadonlyArray<NavItem> = [
  { href: "/chat", label: "새 채팅", pageTitle: "온 에이전트", icon: MessageSquarePlus, exact: true },
  { href: "/settings/notifications", label: "구독 관리", icon: Bell, exact: true },
];

function isActive(pathname: string, item: NavItem): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/**
 * 공통 헤더 + 좌측 햄버거 네비게이션 시트.
 * (chat) 그룹 layout이 흡수해 챗봇·알림 페이지에 공통 적용된다.
 */
export function AppHeader({ title }: AppHeaderProps) {
  const { user, loading, loggingOut, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const activeItem = NAV_ITEMS.find((item) => isActive(pathname, item));
  const pageTitle = title ?? activeItem?.pageTitle ?? activeItem?.label ?? "온서울";

  return (
    // 좌/우 동일 폭(44px) 슬롯으로 중앙 타이틀을 시각적으로 정렬한다.
    // 헤더 패딩(px-4)은 본문(px-4)과 일치시켜 햄버거가 본문 기준에서 어긋나 보이지 않게 한다.
    <header className="grid grid-cols-[44px_1fr_44px] items-center border-b border-border px-4 py-2">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger
          render={
            <button
              type="button"
              aria-label="메뉴 열기"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-foreground transition-colors hover:bg-accent/50"
            />
          }
        >
          <Menu className="size-5" aria-hidden="true" />
        </SheetTrigger>
        <SheetContent
          side="left"
          className="flex w-72 flex-col gap-0 sm:max-w-xs"
        >
          <SheetHeader className="border-b border-border">
            <SheetTitle>온서울</SheetTitle>
            {/*
              base-ui Dialog.Description은 <p>로 렌더된다. Skeleton(div) 같은 블록 요소를
              그 안에 두면 HTML 스펙 위반 + hydration mismatch가 발생하므로,
              짧은 설명 텍스트만 sr-only로 두고 사용자 정보는 별도 div 슬롯으로 분리한다.
            */}
            <SheetDescription className="sr-only">
              사용자 메뉴 및 페이지 네비게이션
            </SheetDescription>
            <div className="flex items-center gap-3 pt-1 text-sm text-muted-foreground">
              {loading ? (
                <Skeleton className="h-4 w-24" />
              ) : user ? (
                <span>{user.nickname}</span>
              ) : (
                <span>로그인이 필요합니다</span>
              )}
            </div>
          </SheetHeader>

          <nav className="flex flex-1 flex-col gap-1 p-2" aria-label="주요 메뉴">
            {NAV_ITEMS.map((item) => {
              const active = isActive(pathname, item);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex min-h-11 items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                    active
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-foreground hover:bg-accent/50",
                  )}
                >
                  <Icon className="size-4" aria-hidden="true" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-border p-2">
            <button
              type="button"
              disabled={loggingOut}
              aria-busy={loggingOut}
              onClick={() => {
                void (async () => {
                  await logout();
                  setOpen(false);
                })();
              }}
              className="flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-foreground transition-colors hover:bg-accent/50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <LogOut className="size-4" aria-hidden="true" />
              <span>{loggingOut ? "로그아웃 중…" : "로그아웃"}</span>
            </button>
          </div>
        </SheetContent>
      </Sheet>

      <h1 className="text-center text-base font-medium">{pageTitle}</h1>

      <div aria-hidden="true">
        {/* 우측 균형용 placeholder — 좌측 햄버거(44px) 슬롯과 동일 폭 유지. */}
      </div>
    </header>
  );
}
