"use client";

import { cn } from "@/lib/utils";

const EXAMPLE_QUESTIONS = [
  "지금 접수 중인 체육시설 관련 서비스 알려줘",
  "자연 속에서 할 수 있는 활동 찾아줘",
  "마포구에서 아이들과 참여할 수 있는 무료 프로그램 알려줘",
] as const;

interface WelcomeMessageProps {
  onQuestion: (question: string) => void;
}

/**
 * 새 채팅 시작 전 안내 버블.
 * messages가 빈 상태(idle)일 때만 렌더되며, 예시 질문 클릭 시 바로 전송한다.
 */
export function WelcomeMessage({ onQuestion }: WelcomeMessageProps) {
  return (
    <div className="flex w-full flex-col gap-3">
      {/* ASSISTANT 말풍선 스타일 */}
      <div className="flex w-full justify-start">
        <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-4 py-3 text-sm leading-relaxed text-foreground">
          <p className="font-medium">안녕하세요! 저는 온 에이전트예요 👋</p>
          <p className="mt-2 text-muted-foreground">
            서울 열린데이터 광장의 공공 API를 활용해 서울시 및 산하기관, 자치구의
            시설대관 및 예약 정보를 수집하고, 원하시는 시설 예약을 쉽게 찾을 수
            있도록 도와드리는 서비스예요.
          </p>
          <p className="mt-2 text-muted-foreground">
            자연어로 질문하시면 관련 시설을 조회해 시설명·접수 상태·기간·예약
            링크를 안내해 드려요.
          </p>
          <p className="mt-3 font-medium">아래 질문을 눌러 바로 시작해 보세요.</p>
        </div>
      </div>

      {/* 예시 질문 버튼 목록 */}
      <div className="flex flex-col gap-2 pl-0">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onQuestion(q)}
            className={cn(
              "self-start rounded-2xl rounded-bl-sm border border-border bg-background px-4 py-2",
              "text-left text-sm text-foreground transition-colors hover:bg-accent/50",
            )}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
