"""공유 컬럼 상수 — public_service_reservations 조회 결과 컬럼 목록.

sql_search 와 hydrate_services 가 동일한 컬럼 셋을 반환하도록
단일 진실 공급원으로 관리한다.
"""

PUBLIC_SERVICE_RESERVATIONS_COLUMNS = """
    service_id, service_name, max_class_name, min_class_name,
    area_name, place_name, service_status, payment_type,
    service_url, receipt_start_dt, receipt_end_dt,
    service_open_start_dt, service_open_end_dt,
    coord_x, coord_y, target_info
"""
