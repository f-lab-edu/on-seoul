import Image from "next/image";

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
 * OAuth 프로바이더 목록. 진입 경로가 `{base}/{id}`로 일정하므로 id만 베이스 URL에 덧붙인다.
 * 각 프로바이더 브랜드 가이드 준수

 * shadcn 원본 버튼은 보존하고 className 확장으로 색상만 덮어씌운다 (CLAUDE.md C.2).
 */
const PROVIDERS = [
  {
    id: "google",
    label: "Google로 시작하기",
    logo: "/auth/google_logo.png",
    logoAlt: "Google 로고",
    logoSize: 20,
    // 브랜드 가이드: 채우기 #F2F2F2, 획 없음, 텍스트 #1F1F1F
    className:
      "bg-[#F2F2F2] text-[#1F1F1F] border-transparent hover:bg-[#E8E8E8] dark:bg-[#F2F2F2] dark:text-[#1F1F1F] dark:hover:bg-[#E8E8E8]",
  },
  {
    id: "kakao",
    label: "Kakao로 시작하기",
    logo: "/auth/kakao_logo.png",
    logoAlt: "카카오 로고",
    // 심볼이 캔버스 끝까지 꽉 차므로 20px 유지
    logoSize: 20,
    // 브랜드 가이드: 컨테이너 #FEE500, 심볼 #000000
    className:
      "bg-[#FEE500] text-black border-transparent hover:bg-[#FDD835] dark:bg-[#FEE500] dark:text-black dark:hover:bg-[#FDD835]",
  },
] as const;

/**
 * 로그인 진입 페이지. OAuth 진입 베이스 URL은 환경변수에서 주입한다.
 * 백엔드가 콜백 응답으로 쿠키를 직접 발급하므로 프론트는 단순 링크만 노출.
 *
 * 베이스 URL 미설정 시 같은 페이지로 재로드되는 것을 막기 위해 버튼을 비활성화하고
 * 에러 메시지를 표시한다. 외부 오리진이므로 Next.js `<Link>` 대신 `<a>`를 사용한다.
 */
export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error } = await searchParams;
  const oauthBaseUrl = process.env.NEXT_PUBLIC_OAUTH_BASE_URL?.replace(/\/$/, "");

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>온서울에 오신 것을 환영합니다</CardTitle>
          <CardDescription>
            소셜 계정으로 로그인하고 서울시 공공서비스 예약을 시작해보세요.
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
          {!oauthBaseUrl && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              로그인 진입 URL이 설정되지 않았습니다. 관리자에게 문의해주세요.
            </p>
          )}
          {PROVIDERS.map((provider) =>
            oauthBaseUrl ? (
              <a
                key={provider.id}
                href={`${oauthBaseUrl}/${provider.id}`}
                className={cn(
                  buttonVariants({ size: "lg", className: "w-full" }),
                  "justify-center gap-2.5 font-bold",
                  provider.className,
                )}
              >
                <Image
                  src={provider.logo}
                  alt={provider.logoAlt}
                  width={provider.logoSize}
                  height={provider.logoSize}
                  className="shrink-0"
                />
                {provider.label}
              </a>
            ) : (
              <button
                key={provider.id}
                type="button"
                disabled
                aria-disabled="true"
                className={cn(
                  buttonVariants({ size: "lg", className: "w-full" }),
                  "justify-center gap-2.5 font-bold",
                  provider.className,
                  "pointer-events-none opacity-50",
                )}
              >
                <Image
                  src={provider.logo}
                  alt={provider.logoAlt}
                  width={provider.logoSize}
                  height={provider.logoSize}
                  className="shrink-0"
                />
                {provider.label}
              </button>
            ),
          )}
        </CardContent>
        <CardFooter className="text-xs text-muted-foreground">
          로그인 시 서비스 이용약관 및 개인정보 처리방침에 동의하게 됩니다.
        </CardFooter>
      </Card>
    </main>
  );
}
