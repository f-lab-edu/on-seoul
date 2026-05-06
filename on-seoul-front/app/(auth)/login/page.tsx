import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface LoginPageProps {
  searchParams: Promise<{ error?: string }>;
}

/**
 * 로그인 진입 페이지. OAuth 진입 URL은 환경변수에서 주입한다.
 * 백엔드가 콜백 응답으로 쿠키를 직접 발급하므로 프론트는 단순 링크만 노출.
 */
export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error } = await searchParams;
  const oauthUrl = process.env.NEXT_PUBLIC_OAUTH_LOGIN_URL ?? "";

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>온서울에 로그인</CardTitle>
          <CardDescription>
            서울시 공공서비스 예약을 도와드릴게요.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {error === "forbidden" && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              계정이 정지되었거나 사용할 수 없습니다. 관리자에게 문의해주세요.
            </p>
          )}
          <Link
            href={oauthUrl}
            className={buttonVariants({ size: "lg", className: "w-full" })}
          >
            Google 계정으로 계속하기
          </Link>
        </CardContent>
        <CardFooter className="text-xs text-muted-foreground">
          로그인 시 서비스 이용약관 및 개인정보 처리방침에 동의하게 됩니다.
        </CardFooter>
      </Card>
    </main>
  );
}
