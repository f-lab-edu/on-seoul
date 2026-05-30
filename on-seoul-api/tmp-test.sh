curl -N -X POST http://office.aift.kr:49891/query \
  -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  -b "access_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwidHlwZSI6ImFjY2VzcyIsImlhdCI6MTc3ODI1NzI3OCwiZXhwIjoxNzc4MjU4MTc4fQ.Ym20nip_glJo8JbcoIy_yj7YAVt2LtU3zagleK6Jnt8" \
  -d '{"question":"주말에 예약 가능한 테니스장 있을까?"}'



curl -N -X POST http://office.aift.kr:49891/auth/logout \
  -H "Content-Type: application/json" \
  -b "access_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwidHlwZSI6ImFjY2VzcyIsImlhdCI6MTc3Nzk1NTY1NywiZXhwIjoxNzc3OTU2NTU3fQ.le6QOA9a6O7UJWiBZMAOFrFalp2xV0bU2sG1FMegASQ"


curl -N -X POST http://office.aift.kr:49892/chat/stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "room_id": 1,
    "message_id": 1,
    "message": "강남구 체육시설 알려줘"
  }' \
  --no-buffer

SQL_SEARCH   - 카테고리(체육시설·문화행사·시설대관·교육·진료), 자치구, 접수 상태, 날짜 등
               구체적 조건으로 시설/서비스를 조회하는 경우
VECTOR_SEARCH - 키워드나 의미로 비슷한 시설을 찾는 경우
MAP          - 지도, 위치, 반경, 근처 시설을 묻는 경우
# 지금 접수 중인 수영장
# 마포구 이번 주 문화행사
# 아이랑 체험할 수 있는 곳
# 조용한 운동 시설
# 주말에 예약 가능한 테니스장 있을까?
# 강남구 체육시설 알려줘
# 풋살장은 어디쪽이 제일 많아?
# 테니스장 어떻게 예약해야해?
# 내 주변 500m 이내 체육관
# 지도로 보여줘
