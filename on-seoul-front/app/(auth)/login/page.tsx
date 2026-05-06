import { buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface LoginPageProps {
  searchParams: Promise<{ error?: string }>;
}

/**
 * 로그인 진입 페이지. OAuth 진입 URL은 환경변수에서 주입한다.
 * 백엔드가 콜백 응답으로 쿠키를 직접 발급하므로 프론트는 단순 링크만 노출.
 *
 * 환경변수 미설정 시 같은 페이지로 재로드되는 것을 막기 위해 버튼을 비활성화하고
 * 에러 메시지를 표시한다. 외부 오리진이므로 Next.js `<Link>` 대신 `<a>`를 사용한다.
 */
export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error } = await searchParams;
  const oauthUrl = process.env.NEXT_PUBLIC_OAUTH_LOGIN_URL;

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
          {!oauthUrl && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              로그인 진입 URL이 설정되지 않았습니다. 관리자에게 문의해주세요.
            </p>
          )}
          {oauthUrl ? (
            <a
              href={oauthUrl}
              rel="noopener noreferrer"
              className={buttonVariants({ size: "lg", className: "w-full" })}
            >
              Google 계정으로 계속하기
            </a>
          ) : (
            <button
              type="button"
              disabled
              aria-disabled="true"
              className={cn(
                buttonVariants({ size: "lg", className: "w-full" }),
                "pointer-events-none opacity-50",
              )}
            >
              Google 계정으로 계속하기
            </button>
          )}
        </CardContent>
        <CardFooter className="text-xs text-muted-foreground">
          로그인 시 서비스 이용약관 및 개인정보 처리방침에 동의하게 됩니다.
        </CardFooter>
      </Card>
    </main>
  );
}
