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
    target_bus = request.args.get('bus', 'all') 
    
    start_loc_name = LOC_NAMES.get(start_loc, "비마관")
    target_station_name = STATION_NAMES.get(target_dir, "정문 앞 정류장")
    distance = DISTANCES[start_loc][target_dir] 
    
    current_time = int(time.time())
    all_buses = []
    
    if target_dir not in MOCK_SCHEDULE:
        MOCK_SCHEDULE[target_dir] = {}
    bus_nums = ["261", "1137", "163"] if target_dir == "11285" else ["163", "1144", "261"]

    for b_num in bus_nums:
        if b_num not in MOCK_SCHEDULE[target_dir]:
            MOCK_SCHEDULE[target_dir][b_num] = []

        MOCK_SCHEDULE[target_dir][b_num] = [t for t in MOCK_SCHEDULE[target_dir][b_num] if t > current_time - 10]

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
                        s_type, s_msg, s_act, s_pri = calculate_action(bus_seconds, distance)
                        all_buses.append({
                            "bus_number": rtNm, "station_name": target_station_name, "distance_str": f"{distance}m", 
                            "seconds": bus_seconds, "status_type": s_type,
                            "message": s_msg, "action_txt": s_act, "priority": s_pri,
                            "path_str": f"{start_loc_name} ➔ {target_station_name}"
                        })

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

        if target_bus != 'all':
            filtered_buses = [b for b in all_buses if b['bus_number'] == target_bus]
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

        # 🚨 핵심 로직: 버스 번호가 겹치면 가장 빨리 오는 1대만 남깁니다.
        all_buses.sort(key=lambda x: x['seconds']) # 먼저 남은 시간(초) 순으로 오름차순 정렬
        
        unique_buses = []
        seen_nums = set()
        
        for b in all_buses:
            if b['bus_number'] not in seen_nums:
                unique_buses.append(b)
                seen_nums.add(b['bus_number'])
                
        all_buses = unique_buses # 중복이 제거된 깔끔한 리스트로 교체

        # 최종적으로 우선순위(뛰어/걸어)와 시간순으로 다시 정렬
        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"데이터 통신 에러가 발생했습니다."})

if __name__ == '__main__':
    app.run(port=8080)
