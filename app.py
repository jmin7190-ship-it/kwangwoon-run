from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import requests
import re
import math
import random
import time  # 현실 시간을 추적하기 위해 추가됨

app = Flask(__name__)
CORS(app)

# 🔑 API 키
ODSAY_API_KEY = "xTses587ntx0ITz4NXkSPbtckAnLk+y5ikHjr+FdoQU"

# 건물에서 각 정류장까지의 실제 거리(m) 테이블
DISTANCES = {
    "bima": {"11285": 150, "11279": 500},     
    "chambit": {"11285": 500, "11279": 850},  
    "library": {"11285": 250, "11279": 600}   
}
LOC_NAMES = {"bima": "비마관", "chambit": "참빛관", "library": "중앙도서관"}
STATION_NAMES = {"11285": "정문 앞 정류장", "11279": "광운대역 정류장"}

STATION_ID_CACHE = {}

# 🚨 핵심: 가짜 버스들의 '도착 시간'을 기억하는 서버 메모리
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
    # 공학적 거리 로직 반영 (1.27m/s = 성인 도보, 4.0m/s = 뜀걸음, 안전 마진 30초)
    walk_speed = 1.27
    run_speed = 4.0
    buffer_time = 30  
    
    walk_time_sec = (distance / walk_speed) + buffer_time
    run_time_sec = (distance / run_speed) + buffer_time
    
    walk_mins = math.ceil(walk_time_sec / 60)
    spare_sec = bus_seconds - walk_time_sec

    # 논리 분기점
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
    
    start_loc_name = LOC_NAMES.get(start_loc, "비마관")
    target_station_name = STATION_NAMES.get(target_dir, "정문 앞 정류장")
    distance = DISTANCES[start_loc][target_dir] 
    
    all_buses = []
    
    try:
        # 1. 실제 공공데이터 API 호출 시도
        station_id = get_odsay_station_id(target_dir)
        res = requests.get("https://api.odsay.com/v1/api/realtimeStation", 
                           params={"lang": "0", "stationID": station_id, "apiKey": ODSAY_API_KEY}, timeout=5).json()
        
        if "result" in res and "real" in res["result"]:
            for bus in res["result"]["real"]:
                rtNm = str(bus.get("routeNm") or bus.get("routeName"))
                if any(b in rtNm for b in ['261', '1144', '1137', '163']):
                    bus_seconds = parse_bus_time(bus)
                    if bus_seconds < 9000:
                        status_type, msg, action_txt, priority = calculate_action(bus_seconds, distance)
                        all_buses.append({
                            "bus_number": rtNm, 
                            "station_name": target_station_name, 
                            "distance_str": f"{distance}m", 
                            "seconds": bus_seconds,
                            "status_type": status_type,
                            "message": msg, "action_txt": action_txt, "priority": priority,
                            "path_str": f"{start_loc_name} ➔ {target_station_name}"
                        })

        # 2. 공공데이터가 없거나 통신 실패 시 -> 가상 스케줄러(Mock) 가동
        if not all_buses:
            current_time = int(time.time()) # 현재 현실 시간 추적
            
            # 처음 호출된 방향이면 빈 리스트 생성
            if target_dir not in MOCK_SCHEDULE:
                MOCK_SCHEDULE[target_dir] = []
                
            # (핵심 1) 이미 도착 시간이 지나간(0초 미만) 버스는 삭제
            MOCK_SCHEDULE[target_dir] = [b for b in MOCK_SCHEDULE[target_dir] if b['arrival_time'] > current_time]
            
            # (핵심 2) 화면에 표시할 버스가 3대 미만이면 뒤에 새 버스 배차
            bus_nums = ["261", "1137", "163"] if target_dir == "11285" else ["163", "1144", "261"]
            
            while len(MOCK_SCHEDULE[target_dir]) < 3:
                if MOCK_SCHEDULE[target_dir]:
                    # 이전 버스가 있다면 그 버스보다 2~4분 뒤에 오도록 배차
                    last_arrival = max(b['arrival_time'] for b in MOCK_SCHEDULE[target_dir])
                    new_arrival = last_arrival + random.randint(120, 240)
                else:
                    # 첫 버스는 지금으로부터 1~3분 뒤 도착
                    new_arrival = current_time + random.randint(60, 180)
                
                # 번호가 겹치지 않게 배정
                used_nums = [b['num'] for b in MOCK_SCHEDULE[target_dir]]
                avail_nums = [n for n in bus_nums if n not in used_nums]
                b_num = avail_nums[0] if avail_nums else random.choice(bus_nums)
                
                MOCK_SCHEDULE[target_dir].append({
                    "num": b_num,
                    "arrival_time": new_arrival
                })
            
            # (핵심 3) 배차된 가상 버스들의 '남은 시간'을 계산하여 프론트엔드에 전달
            for b in MOCK_SCHEDULE[target_dir]:
                sec_left = b['arrival_time'] - current_time # 현실 시간만큼 깎인 정확한 남은 시간
                s_type, s_msg, s_act, s_pri = calculate_action(sec_left, distance) 
                
                all_buses.append({
                    "bus_number": b['num'], 
                    "station_name": target_station_name, 
                    "distance_str": f"{distance}m", 
                    "seconds": sec_left, 
                    "status_type": s_type, 
                    "message": s_msg, 
                    "action_txt": s_act, 
                    "priority": s_pri, 
                    "path_str": f"{start_loc_name} ➔ {target_station_name}"
                })

        # 최종 정렬 및 전송
        all_buses.sort(key=lambda x: (x['priority'], x['seconds']))
        return jsonify({"status": "success", "best_bus": all_buses[0], "bus_list": all_buses})
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"데이터 로딩 중 에러가 발생했습니다."})

if __name__ == '__main__':
    app.run(port=8080)
