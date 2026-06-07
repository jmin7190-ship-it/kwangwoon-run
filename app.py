from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re
import math
import random
import time

app = Flask(__name__)
CORS(app)

ODSAY_API_KEY = "xTses587ntx0ITz4NXkSPbtckAnLk+y5ikHjr+FdoQU"

# 물리적 거리 테이블 (m)
DISTANCES = {
    "bima": {"11285": 150, "11279": 500},     
    "chambit": {"11285": 500, "11279": 850},  
    "library": {"11285": 250, "11279": 600}   
}

# 🚨 현실 고증 3: 지형/신호등 패널티 타임 (초) 추가
# 단순 물리적 거리 외에 횡단보도, 언덕 등을 고려한 추가 지연 시간
TOPOGRAPHY_PENALTY = {
    "bima_11279": 120,    # 비마관 -> 광운대역 (횡단보도 2개 대기)
    "chambit_11279": 150, # 참빛관 -> 광운대역 (언덕길 + 횡단보도)
    "chambit_11285": 60,  # 참빛관 -> 정문 (약간의 내리막 및 횡단보도)
    "library_11285": 30,  # 도서관 -> 정문 (비교적 평탄)
}

LOC_NAMES = {"bima": "비마관", "chambit": "참빛관", "library": "중앙도서관"}
STATION_NAMES = {"11285": "정문 앞 정류장", "11279": "광운대역 정류장"}
STATION_ID_CACHE = {}
MOCK_SCHEDULE = {}

def parse_bus_time(bus):
    try:
        bus_str = str(bus)
        m_match = re.search(r'(\d+)분', bus_str)
        s_match = re.search(r'(\d+)초', bus_str)
        if m_match or s_match:
            minutes = int(m_match.group(1)) if m_match else 0
            seconds = int(s_match.group(1)) if s_match else 0
            return (minutes * 60) + seconds
        tra_match = re.search(r"['\"](?:traTime1|predictTime1|time1)['\"]\s*:\s*['\"]?(\d+)['\"]?", bus_str)
        if tra_match:
            val = int(tra_match.group(1))
            return (val * 60) if val < 100 else val
        return 9999
    except:
        return 9999

def calculate_action(bus_seconds, distance, penalty):
    walk_speed = 1.27; run_speed = 4.0; buffer_time = 30  
    
    # 지형 패널티가 합산된 진짜 도보 소요 시간
    walk_time_sec = (distance / walk_speed) + buffer_time + penalty
    run_time_sec = (distance / run_speed) + buffer_time + penalty
    
    walk_mins = math.ceil(walk_time_sec / 60)
    
    # 이미 버스가 도착해서 대기 중(마이너스 시간)이라면 0으로 보정
    display_sec = max(0, bus_seconds)
    spare_sec = display_sec - walk_time_sec

    if spare_sec >= 60: 
        spare_mins = int(spare_sec // 60)
        return "walk", f"이동에 {walk_mins}분(신호대기 포함) 소요. {spare_mins}분 더 여유가 있어요!", "걷기", 1
    elif spare_sec >= 0:
        return "walk", f"이동에 {walk_mins}분 소요됩니다. 지금 출발해야 안전하게 타요!", "빠른 걸음", 1
    elif display_sec >= run_time_sec: 
        return "run", f"거리가 멉니다({distance}m). 지금 당장 뛰어야 탈 수 있어요!", "뛰기", 2
    else: 
        return "giveup", f"이동시간({walk_mins}분) 고려 시 뛰어도 늦습니다. 포기하세요.", "포기", 3

def get_odsay_station_id(ars_id):
    if ars_id in STATION_ID_CACHE: return STATION_ID_CACHE[ars_id]
    for name in ["광운대", "광운대학교", "광운대역"]:
        res = requests.get("https://api.odsay.com/v1/api/searchStation", 
                           params={"lang": "0", "stationName": name, "stationClass": "1", "count": "100", "apiKey": ODSAY_API_KEY}, timeout=5).json()
        if "result" in res and "station" in res["result"]:
            for st in res["result"]["station"]:
                if str(st.get("arsID", "")).replace("-", "") == ars_id:
                    STATION_ID_CACHE[ars_id] = st.get("stationID")
                    return STATION_ID_CACHE[ars_id]
    raise Exception(f"{ars_id} 정류장을 찾을 수 없습니다.")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/bus')
def get_bus_data():
    start_loc = request.args.get('start', 'bima')
    target_dir = request.args.get('dir', '11285') 
    target_bus = request.args.get('bus', 'all') 
    
    start_loc_name = LOC_NAMES.get(start_loc, "비마관")
    target_station_name = STATION_NAMES.get(target_dir, "정문 앞 정류장")
    distance = DISTANCES[start_loc][target_dir] 
    
    # 건물-정류장 조합에 따른 패널티 산출 (없으면 기본 30초)
    route_key = f"{start_loc}_{target_dir}"
    penalty = TOPOGRAPHY_PENALTY.get(route_key, 30)
    
    current_time = int(time.time())
    all_buses = []
    
    if target_dir not in MOCK_SCHEDULE:
        MOCK_SCHEDULE[target_dir] = {}
    bus_nums = ["261", "1137", "163"] if target_dir == "11285" else ["163", "1144", "261"]

    for b_num in bus_nums:
        if b_num not in MOCK_SCHEDULE[target_dir]:
            MOCK_SCHEDULE[target_dir][b_num] = []

        # 🚨 현실 고증 2: 도착(0초) 후 90초 동안은 리스트에서 지우지 않음 ('곧 도착'으로 정차 유지)
        MOCK_SCHEDULE[target_dir][b_num] = [t for t in MOCK_SCHEDULE[target_dir][b_num] if t > current_time - 90]

        # 🚨 현실 고증 1: 돌발 교통체증 Jitter 발생 (20% 확률로 도착 예정 시간 10~20초 추가 지연)
        for i in range(len(MOCK_SCHEDULE[target_dir][b_num])):
            if MOCK_SCHEDULE[target_dir][b_num][i] > current_time: 
                if random.random() < 0.20:
                    MOCK_SCHEDULE[target_dir][b_num][i] += random.randint(10, 20)

        # 배차 충전
        while len(MOCK_SCHEDULE[target_dir][b_num]) < 2:
            if MOCK_SCHEDULE[target_dir][b_num]:
                last_arrival = max(MOCK_SCHEDULE[target_dir][b_num])
                new_arr = last_arrival + random.randint(300, 600)
            else:
                new_arr = current_time + random.randint(30, 240)
            MOCK_SCHEDULE[target_dir][b_num].append(new_arr)

    try:
        station_id = get_odsay_station_id(target_dir)
        res = requests.get("https://api.odsay.com/v1/api/realtimeStation", 
                           params={"lang": "0", "stationID": station_id, "apiKey": ODSAY_API_KEY}, timeout=5).json()
        
        if "result" in res and "real" in res["result"]:
            for bus in res["result"]["real"]:
                rtNm = str(bus.get("routeNm") or bus.get("routeName"))
                if any(b in rtNm for b in bus_nums):
                    bus_seconds = parse_bus_time(bus)
                    if bus_seconds < 9000:
                        s_type, s_msg, s_act, s_pri = calculate_action(bus_seconds, distance, penalty)
                        all_buses.append({
                            "bus_number": rtNm, "station_name": target_station_name, "distance_str": f"{distance}m", 
                            "seconds": max(0, bus_seconds), "status_type": s_type,
                            "message": s_msg, "action_txt": s_act, "priority": s_pri,
                            "path_str": f"{start_loc_name} ➔ {target_station_name}"
                        })

        if not all_buses:
            for b_num in bus_nums:
                for arr_time in MOCK_SCHEDULE[target_dir][b_num]:
                    sec_left = arr_time - current_time
                    s_type, s_msg, s_act, s_pri = calculate_action(sec_left, distance, penalty)
                    all_buses.append({
                        "bus_number": b_num, "station_name": target_station_name, "distance_str": f"{distance}m", 
                        "seconds": max(0, sec_left), "status_type": s_type, # 0초 미만은 모두 0초(곧 도착)로 전송
                        "message": s_msg, "action_txt": s_act, "priority": s_pri,
                        "path_str": f"{start_loc_name} ➔ {target_station_name}"
                    })

        if target_bus != 'all':
            filtered_buses = [b for b in all_buses if b['bus_number'] == target_bus]
            if not filtered_buses and target_bus in bus_nums:
                for arr_time in MOCK_SCHEDULE[target_dir][target_bus]:
                    sec_left = arr_time - current_time
                    s_type, s_msg, s_act, s_pri = calculate_action(sec_left, distance, penalty)
                    filtered_buses.append({
                        "bus_number": target_bus, "station_name": target_station_name, "distance_str": f"{distance}m", 
                        "seconds": max(0, sec_left), "status_type": s_type,
                        "message": s_msg, "action_txt": s_act, "priority": s_pri,
                        "path_str": f"{start_loc_name} ➔ {target_station_name}"
                    })
            all_buses = filtered_buses

        if not all_buses:
            return jsonify({"status": "error", "message": "해당 노선의 운행 정보가 없습니다."})

        # 번호 겹침 제거 (가장 빠른 차만 남김)
        all_buses.sort(key=lambda x: x['seconds'])
        unique_buses = []
        seen_nums = set()
        for b in all_buses:
            if b['bus_number'] not in seen_nums:
                unique_buses.append(b)
                seen_nums.add(b['bus_number'])
        all_buses = unique_buses 

        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"데이터 통신 에러가 발생했습니다."})

if __name__ == '__main__':
    app.run(port=8080)
