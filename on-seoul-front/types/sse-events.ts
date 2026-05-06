/**
 * SSE 이벤트 타입 — 백엔드 on-seoul-agent/schemas/events.py 의 미러.
 * 백엔드 스키마가 변경되면 이 파일을 동기화한다. 어긋나면 frontend-qa가 차단.
 *
 * 정본: on-seoul-agent/schemas/events.py
 */

export type AgentName = "router" | "sql" | "vector" | "answer";

export type ToolName = "sql_search" | "vector_search" | "map_search";

export type SseEvent =
  | { type: "agent_start"; agent: AgentName }
  | { type: "tool_call"; tool: ToolName; args: unknown }
  | { type: "tool_result"; tool: string; ok: boolean }
  | { type: "token"; delta: string }
  | { type: "done"; messageId: number }
  | { type: "error"; message: string };
