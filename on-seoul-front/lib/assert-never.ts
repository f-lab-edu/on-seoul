/**
 * discriminated union switch의 default 분기에서 사용.
 * 새 이벤트 타입이 추가됐는데 switch에 빠졌으면 컴파일 에러로 잡아준다.
 */
export function assertNever(value: never): never {
  throw new Error(`Unhandled discriminated union case: ${JSON.stringify(value)}`);
}
