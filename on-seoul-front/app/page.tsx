import { redirect } from "next/navigation";

/** 루트(`/`)는 AI 에이전트 채팅 페이지로 바로 이동. */
export default function RootPage() {
  redirect("/chat");
}
