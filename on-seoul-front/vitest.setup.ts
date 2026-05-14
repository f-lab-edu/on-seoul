import { beforeEach } from "vitest";

/**
 * 테스트 공통 환경 초기화.
 * api-client / server-api 가 의존하는 환경변수를 항상 결정적으로 세팅한다.
 * 개별 테스트는 필요 시 자체 beforeEach에서 덮어쓰면 된다.
 */
beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.test.local";
  process.env.API_BASE_URL = "https://api.test.local";
});
