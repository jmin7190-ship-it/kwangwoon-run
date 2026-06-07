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

DISTANCES = {
    "bima": {"11285": 150, "11279": 500},     
    "chambit": {"11285": 500, "11279": 850},  
    "library": {"11285": 250, "11279": 600}   
}
LOC_NAMES = {"bima": "비마관", "chambit": "참빛관", "library": "중앙도서관"}
STATION_NAMES = {"11285": "정문 앞 정류장", "11279": "광운대역 정류장"}
STATION_ID_CACHE = {}

# 버스 노선별 도착 일정을 백그라운드에서 기억하는 메모리
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

def calculate_action(bus_seconds, distance):
    walk_speed = 1.27; run_speed = 4.0; buffer_time = 30  
    
    walk_time_sec = (distance / walk_speed) + buffer_time
    run_time_sec = (distance / run_speed) + buffer_time
    
    walk_mins = math.ceil(walk_time_sec / 60)
    spare_sec = bus_seconds - walk_time_sec

    if spare_sec >= 60: 
        spare_mins = int(spare_sec // 60)
        return "walk", f"이동에 {walk_mins}분 소요되며, {spare_mins}분 정도 더 여유가 있어요!", "걷기", 1
    elif spare_sec >= 0:
        return "walk", f"이동에 {walk_mins}분 소요됩니다. 지금 출발해야 안전하게 타요!", "빠른 걸음", 1
    elif bus_seconds >= run_time_sec: 
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
    target_bus = request.args.get('bus', 'all') # 프론트엔드가 선택한 특정 노선 번호
    
    start_loc_name = LOC_NAMES.get(start_loc, "비마관")
    target_station_name = STATION_NAMES.get(target_dir, "정문 앞 정류장")
    distance = DISTANCES[start_loc][target_dir] 
    
    current_time = int(time.time())
    all_buses = []
    
    # 방향에 따른 버스 노선 초기화
    if target_dir not in MOCK_SCHEDULE:
        MOCK_SCHEDULE[target_dir] = {}
    bus_nums = ["261", "1137", "163"] if target_dir == "11285" else ["163", "1144", "261"]

    # 🚨 배차 무한 생성기 로직 (항상 백그라운드에서 동작)
    for b_num in bus_nums:
        if b_num not in MOCK_SCHEDULE[target_dir]:
            MOCK_SCHEDULE[target_dir][b_num] = []

        # 1. 이미 도착해서 떠난 버스(도착시간 < 현재시간 - 10초)는 스케줄에서 과감히 삭제!
        MOCK_SCHEDULE[target_dir][b_num] = [t for t in MOCK_SCHEDULE[target_dir][b_num] if t > current_time - 10]

        # 2. 버스가 비어있으면 5~10분 간격으로 다음 배차를 자동으로 무한 충전!
        while len(MOCK_SCHEDULE[target_dir][b_num]) < 2:
            if MOCK_SCHEDULE[target_dir][b_num]:
                last_arrival = max(MOCK_SCHEDULE[target_dir][b_num])
                new_arr = last_arrival + random.randint(300, 600) # 이전 버스로부터 5~10분 뒤
            else:
                new_arr = current_time + random.randint(30, 240) # 30초~4분 이내 즉시 배차
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
                        s_type, s_msg, s_act, s_pri = calculate_action(bus_seconds, distance)
                        all_buses.append({
                            "bus_number": rtNm, "station_name": target_station_name, "distance_str": f"{distance}m", 
                            "seconds": bus_seconds, "status_type": s_type,
                            "message": s_msg, "action_txt": s_act, "priority": s_pri,
                            "path_str": f"{start_loc_name} ➔ {target_station_name}"
                        })

        # 실시간 데이터가 없거나 에러 시 백그라운드의 가상 스케줄러 데이터를 꺼내옴
        if not all_buses:
            for b_num in bus_nums:
                for arr_time in MOCK_SCHEDULE[target_dir][b_num]:
                    sec_left = arr_time - current_time
                    s_type, s_msg, s_act, s_pri = calculate_action(sec_left, distance)
                    all_buses.append({
                        "bus_number": b_num, "station_name": target_station_name, "distance_str": f"{distance}m", 
                        "seconds": sec_left, "status_type": s_type,
                        "message": s_msg, "action_txt": s_act, "priority": s_pri,
                        "path_str": f"{start_loc_name} ➔ {target_station_name}"
                    })

        # 🚨 노선 필터링 로직: 사용자가 특정 버스를 선택했다면, 그 버스만 남깁니다.
        if target_bus != 'all':
            filtered_buses = [b for b in all_buses if b['bus_number'] == target_bus]
            
            # (안전망) 사용자가 고른 버스가 하필 실시간 데이터에서 빠져있다면, 가상 스케줄에서 그 버스만 강제로 빌려옵니다.
            if not filtered_buses and target_bus in bus_nums:
                for arr_time in MOCK_SCHEDULE[target_dir][target_bus]:
                    sec_left = arr_time - current_time
                    s_type, s_msg, s_act, s_pri = calculate_action(sec_left, distance)
                    filtered_buses.append({
                        "bus_number": target_bus, "station_name": target_station_name, "distance_str": f"{distance}m", 
                        "seconds": sec_left, "status_type": s_type,
                        "message": s_msg, "action_txt": s_act, "priority": s_pri,
                        "path_str": f"{start_loc_name} ➔ {target_station_name}"
                    })
            all_buses = filtered_buses

        if not all_buses:
            return jsonify({"status": "error", "message": "해당 노선의 운행 정보가 없습니다."})

        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
    except Exception as e:
        return jsonify({"status": "error", "message": f"데이터 통신 에러가 발생했습니다."})

if __name__ == '__main__':
    app.run(port=8080)
